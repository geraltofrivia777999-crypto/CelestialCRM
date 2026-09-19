/*
 * Расчет ЗП — «Настройки → Расчет ЗП».
 *
 * Правило почти никогда не состоит из одной строки: процент от профита плюс
 * сетка по профиту команды плюс фиксированный оклад. Поэтому форма собирается
 * из компонентов, а не из одного поля формулы: их добавляют, переставляют и
 * удаляют по одному, и каждый вид компонента показывает только свои поля.
 *
 * База — не свободный текст, а выбор из двух показателей Финансов: профит
 * самого человека и профит его команды. Опечатка в свободной формуле
 * обернулась бы неверной зарплатой, о которой узнают уже после выплаты.
 *
 * Сохранённое правило применяется в Финансах: блок «Шкала зарплаты» показывает
 * его ступени и по ним же считает выплату.
 */
(function () {
  "use strict";

  var api = window.CelestialAPI;

  var state = {
    user: null,
    canManage: false,
    rules: [],
    roles: [],
    people: [],
    reference: null,
    editing: null,
    components: []
  };

  var KIND_LABELS = {
    percent: "Процент",
    fixed: "Фикс",
    grid: "Сетка",
    deduction: "Вычет"
  };
  var MODE_HINTS = {
    replace: "Правило заменит собой все правила с более широкой привязкой: " +
      "именное отменит ролевое, ролевое — общее.",
    add: "Компоненты правила добавятся к тому, что уже дали правила с более " +
      "широкой привязкой."
  };

  function byId(id) { return document.getElementById(id); }

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function money(value) {
    var amount = Number(value || 0);
    return amount.toLocaleString("ru-RU", {
      minimumFractionDigits: 2, maximumFractionDigits: 2
    });
  }

  /* Число в поле ввода без хвостовых нулей: сервер отдаёт «5.0000», а правят
     руками «5». Точку меняем на запятую только на показ — в number-поле
     браузер ждёт точку, поэтому здесь именно она. */
  function numberValue(value) {
    if (value === null || value === undefined || value === "") return "";
    var amount = Number(value);
    if (!isFinite(amount)) return String(value);
    return String(Math.round(amount * 10000) / 10000);
  }

  function hasPermission(user, code) {
    if (!user || !user.role) return false;
    return (user.role.permissions || []).some(function (permission) {
      return permission.code === "*" || permission.code === code;
    });
  }

  function askConfirm(options) {
    if (window.CelestialShell && window.CelestialShell.confirm) {
      return window.CelestialShell.confirm(options);
    }
    return Promise.resolve(window.confirm(options.message || options.title));
  }

  function notify(options) {
    if (window.CelestialShell && window.CelestialShell.notify) {
      return window.CelestialShell.notify(options);
    }
    window.alert(options.message || options.title);
    return Promise.resolve(true);
  }

  /* ---------- список правил ---------- */

  function ruleSummary(rule) {
    if (rule.scope === "user") return "Пользователь: " + (rule.user_name || "—");
    return "Роль: " + (rule.role_name || "—");
  }

  function periodLabel(rule) {
    if (!rule.valid_from && !rule.valid_to) return "бессрочно";
    return (rule.valid_from ? "с " + rule.valid_from : "всегда") +
      (rule.valid_to ? " по " + rule.valid_to : "");
  }

  function componentChip(component) {
    var text = KIND_LABELS[component.kind] || component.kind;
    if (component.kind === "fixed") {
      text += ": " + money(component.amount);
    } else if (component.kind === "grid") {
      text += ": " + (component.tiers || []).length + " уровней · " +
        (component.base_label || component.base || "—");
    } else if (component.kind === "deduction") {
      text += ": " + (component.base
        ? numberValue(component.percent) + "% от «" +
          (component.base_label || component.base) + "»"
        : money(component.amount));
    } else {
      text += ": " + numberValue(component.percent) + "% от «" +
        (component.base_label || component.base || "—") + "»";
    }
    return '<span style="display:inline-flex;align-items:center;background:#F4F0F0;' +
      'border-radius:8px;padding:5px 9px;font-size:11px;font-weight:700;color:#5A5151">' +
      escapeHtml(text) + "</span>";
  }

  function renderRules() {
    var host = byId("salaryRules");
    if (!host) return;
    var count = byId("settingsSalaryTabCount");
    if (count) count.textContent = state.rules.length;
    if (!state.rules.length) {
      host.innerHTML = '<div style="background:#fff;border:1px dashed #DDD5D5;border-radius:16px;' +
        'padding:28px;text-align:center;color:#857D7D;font-size:12.5px;font-weight:600;' +
        'line-height:1.6">Правил пока нет.<br>' +
        (state.canManage
          ? "Пока их нет, зарплата в Финансах считается прежней лестницей."
          : "Создать правило может финансист или администратор.") + "</div>";
      return;
    }
    host.innerHTML = state.rules.map(function (rule) {
      var off = rule.status !== "active";
      return '<article style="background:#fff;border:1px solid #EBE6E6;border-radius:16px;' +
        'padding:17px 20px' + (off ? ";opacity:.62" : "") + '">' +
        '<div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">' +
        '<div style="flex:1;min-width:200px">' +
        '<div style="font-size:14px;font-weight:700">' + escapeHtml(rule.name) + "</div>" +
        '<div style="font-size:11.5px;color:#9B9292;font-weight:600;margin-top:4px">' +
        escapeHtml(ruleSummary(rule)) + " · " + escapeHtml(periodLabel(rule)) + " · " +
        (rule.mode === "replace" ? "заменяет предыдущие" : "дополняет предыдущие") +
        "</div></div>" +
        '<div class="row-side"><span class="row-status ' +
        (off ? "row-status--off" : "row-status--on") + '">' +
        (off ? "Выключено" : "Активно") + "</span>" +
        (state.canManage
          ? '<button type="button" class="row-btn" data-salary-edit="' +
            escapeHtml(rule.id) + '">Изменить</button>'
          : "") +
        "</div></div>" +
        '<div style="display:flex;gap:7px;flex-wrap:wrap;margin-top:12px">' +
        (rule.components || []).map(componentChip).join("") +
        "</div></article>";
    }).join("");
  }

  /* ---------- форма правила ---------- */

  function optionsHtml(items, selected) {
    return items.map(function (item) {
      return '<option value="' + escapeHtml(item.code || item.id) + '"' +
        ((item.code || item.id) === selected ? " selected" : "") + ">" +
        escapeHtml(item.label || item.name) + "</option>";
    }).join("");
  }

  function baseSelectHtml(index, selected) {
    return '<select class="form-input settings-select" data-part="base" data-index="' + index +
      '">' + optionsHtml(state.reference.bases.map(function (item) {
        return { code: item.code, label: item.label };
      }), selected) + "</select>";
  }

  function tiersHtml(index, tiers) {
    return '<div data-tiers="' + index + '" style="display:grid;gap:9px">' +
      tiers.map(function (tier, level) {
        return '<div style="display:grid;grid-template-columns:1fr 1fr auto;gap:9px;' +
          'align-items:end" data-tier="' + level + '">' +
          '<label><span class="field-label">До суммы</span>' +
          '<input class="form-input" type="number" step="0.01" data-part="tier_up_to" ' +
          'data-index="' + index + '" data-level="' + level + '" value="' +
          escapeHtml(numberValue(tier.up_to)) +
          '" placeholder="пусто = и выше"></label>' +
          '<label><span class="field-label">Процент, %</span>' +
          '<input class="form-input" type="number" step="0.01" data-part="tier_percent" ' +
          'data-index="' + index + '" data-level="' + level + '" value="' +
          escapeHtml(numberValue(tier.percent)) + '"></label>' +
          '<button type="button" data-tier-remove="' + index + ":" + level +
          '" style="height:44px;border:1px solid #F1D9D9;border-radius:10px;background:#fff;' +
          'color:#B91414;padding:0 12px;font-size:11.5px;font-weight:700">Убрать</button></div>';
      }).join("") +
      '<button type="button" data-tier-add="' + index + '" style="justify-self:start;height:34px;' +
      'border:1px solid #E5DFDF;border-radius:9px;background:#fff;padding:0 12px;font-size:11.5px;' +
      'font-weight:700">+ Добавить уровень</button></div>';
  }

  function componentHtml(component, index) {
    var body;
    if (component.kind === "fixed") {
      body = '<label><span class="field-label">Сумма</span>' +
        '<input class="form-input" type="number" step="0.01" data-part="amount" data-index="' +
        index + '" value="' + escapeHtml(numberValue(component.amount)) + '"></label>';
    } else if (component.kind === "grid") {
      body = '<label style="grid-column:1/-1"><span class="field-label">База</span>' +
        baseSelectHtml(index, component.base) + "</label>" +
        '<div style="grid-column:1/-1"><span class="field-label">Сетка: диапазоны и проценты</span>' +
        tiersHtml(index, component.tiers) + "</div>";
    } else if (component.kind === "deduction") {
      body = '<label><span class="field-label">База (пусто = фиксированная сумма)</span>' +
        '<select class="form-input settings-select" data-part="base" data-index="' + index + '">' +
        '<option value="">Фиксированная сумма</option>' +
        optionsHtml(state.reference.bases.map(function (item) {
          return { code: item.code, label: item.label };
        }), component.base) + "</select></label>" +
        (component.base
          ? '<label><span class="field-label">Процент, %</span>' +
            '<input class="form-input" type="number" step="0.01" data-part="percent" ' +
            'data-index="' + index + '" value="' + escapeHtml(numberValue(component.percent)) + '"></label>'
          : '<label><span class="field-label">Сумма вычета</span>' +
            '<input class="form-input" type="number" step="0.01" data-part="amount" ' +
            'data-index="' + index + '" value="' + escapeHtml(numberValue(component.amount)) + '"></label>');
    } else {
      body = '<label><span class="field-label">База</span>' +
        baseSelectHtml(index, component.base) + "</label>" +
        '<label><span class="field-label">Процент, %</span>' +
        '<input class="form-input" type="number" step="0.01" data-part="percent" data-index="' +
        index + '" value="' + escapeHtml(numberValue(component.percent)) + '"></label>';
    }
    return '<div style="border:1px solid #EBE6E6;border-radius:14px;padding:15px 17px;' +
      'background:#FCFBFB">' +
      '<div style="display:flex;align-items:center;justify-content:space-between;gap:12px;' +
      'margin-bottom:12px">' +
      '<span style="font-size:11.5px;font-weight:700;color:#857D7D">#' + (index + 1) +
      " — " + escapeHtml(KIND_LABELS[component.kind] || component.kind) + "</span>" +
      '<button type="button" data-salary-remove="' + index + '" style="height:32px;' +
      'border:1px solid #F1D9D9;border-radius:9px;background:#fff;color:#B91414;padding:0 12px;' +
      'font-size:11.5px;font-weight:700">Удалить</button></div>' +
      '<div style="display:grid;grid-template-columns:1fr 1fr;gap:14px">' + body +
      "</div></div>";
  }

  function renderComponents() {
    var host = byId("salaryComponents");
    host.innerHTML = state.components.length
      ? state.components.map(componentHtml).join("")
      : '<div style="border:1px dashed #DDD5D5;border-radius:12px;padding:18px;text-align:center;' +
        'font-size:12px;color:#9B9292;font-weight:600">Добавьте компонент кнопками выше</div>';
  }

  function renderTarget() {
    var scope = byId("salaryScope").value;
    var label = byId("salaryTargetLabel");
    var select = byId("salaryTarget");
    label.textContent = scope === "user" ? "Пользователь" : "Роль";
    var items = scope === "user" ? state.people : state.roles;
    var current = state.editing
      ? (scope === "user" ? state.editing.user_id : state.editing.role_id)
      : null;
    select.innerHTML = '<option value="">' +
      (scope === "user" ? "Выберите пользователя" : "Выберите роль") + "</option>" +
      items.map(function (item) {
        return '<option value="' + escapeHtml(item.id) + '"' +
          (item.id === current ? " selected" : "") + ">" +
          escapeHtml(item.name) + "</option>";
      }).join("");
  }

  function renderModeHint() {
    byId("salaryModeHint").textContent = MODE_HINTS[byId("salaryMode").value] || "";
  }

  function newComponent(kind) {
    var base = (state.reference.bases[0] || {}).code || "";
    return {
      kind: kind,
      base: kind === "fixed" || kind === "deduction" ? "" : base,
      percent: kind === "percent" ? "5" : "",
      amount: "",
      tiers: kind === "grid" ? [{ up_to: "5000", percent: "10" }] : []
    };
  }

  function readComponents() {
    var host = byId("salaryComponents");
    Array.prototype.forEach.call(host.querySelectorAll("[data-part]"), function (input) {
      var index = Number(input.getAttribute("data-index"));
      var component = state.components[index];
      if (!component) return;
      var part = input.getAttribute("data-part");
      if (part === "tier_up_to" || part === "tier_percent") {
        var level = Number(input.getAttribute("data-level"));
        if (!component.tiers[level]) return;
        component.tiers[level][part === "tier_up_to" ? "up_to" : "percent"] = input.value;
        return;
      }
      component[part] = input.value;
    });
  }

  function openRuleForm(ruleId) {
    var rule = ruleId
      ? state.rules.filter(function (row) { return row.id === ruleId; })[0]
      : null;
    if (ruleId && !rule) return;
    state.editing = rule;
    state.components = rule
      ? (rule.components || []).map(function (component) {
          return {
            kind: component.kind,
            base: component.base || "",
            percent: component.percent === null ? "" : component.percent,
            amount: component.amount === null ? "" : component.amount,
            tiers: (component.tiers || []).map(function (tier) {
              return { up_to: tier.up_to, percent: tier.percent };
            })
          };
        })
      : [newComponent("percent")];

    byId("salaryModalTitle").textContent = rule ? "Правило: изменение" : "Правило: создание";
    byId("salaryName").value = rule ? rule.name : "";
    byId("salaryStatus").value = rule ? rule.status : "active";
    byId("salaryMode").innerHTML = optionsHtml(
      state.reference.modes, rule ? rule.mode : "replace"
    );
    byId("salaryScope").innerHTML = optionsHtml(
      state.reference.scopes, rule ? rule.scope : "role"
    );
    byId("salaryFrom").value = rule && rule.valid_from ? rule.valid_from : "";
    byId("salaryTo").value = rule && rule.valid_to ? rule.valid_to : "";
    byId("salaryDelete").style.display = rule ? "" : "none";
    formError("");
    renderTarget();
    renderModeHint();
    renderComponents();
    byId("salaryModal").classList.add("open");
    byId("salaryName").focus();
  }

  function closeRuleForm() {
    byId("salaryModal").classList.remove("open");
    state.editing = null;
  }

  function formError(message) {
    var host = byId("salaryError");
    host.textContent = message || "";
    host.style.display = message ? "" : "none";
  }

  function payloadFromForm() {
    readComponents();
    var scope = byId("salaryScope").value;
    var target = byId("salaryTarget").value;
    return {
      name: byId("salaryName").value.trim(),
      status: byId("salaryStatus").value,
      mode: byId("salaryMode").value,
      scope: scope,
      role_id: scope === "role" ? target || null : null,
      user_id: scope === "user" ? target || null : null,
      valid_from: byId("salaryFrom").value || null,
      valid_to: byId("salaryTo").value || null,
      components: state.components.map(function (component) {
        var row = { kind: component.kind };
        if (component.kind === "fixed") {
          row.amount = component.amount === "" ? null : Number(component.amount);
        } else if (component.kind === "grid") {
          row.base = component.base;
          row.tiers = component.tiers
            .filter(function (tier) { return String(tier.percent).trim() !== ""; })
            .map(function (tier) {
              return {
                up_to: String(tier.up_to).trim() === "" ? null : Number(tier.up_to),
                percent: Number(tier.percent)
              };
            });
        } else if (component.kind === "deduction") {
          if (component.base) {
            row.base = component.base;
            row.percent = component.percent === "" ? null : Number(component.percent);
          } else {
            row.amount = component.amount === "" ? null : Number(component.amount);
          }
        } else {
          row.base = component.base;
          row.percent = component.percent === "" ? null : Number(component.percent);
        }
        return row;
      })
    };
  }

  async function saveRule() {
    var payload = payloadFromForm();
    if (!payload.name) return formError("Укажите название правила");
    if (!payload.components.length) return formError("Добавьте хотя бы один компонент формулы");
    byId("salarySave").disabled = true;
    try {
      if (state.editing) {
        await api.patch("/salary/rules/" + state.editing.id, payload);
      } else {
        await api.post("/salary/rules", payload);
      }
      closeRuleForm();
      await loadRules();
    } catch (error) {
      formError(error && error.message ? error.message : "Не удалось сохранить правило");
    } finally {
      byId("salarySave").disabled = false;
    }
  }

  async function deleteRule() {
    if (!state.editing) return;
    var rule = state.editing;
    if (!(await askConfirm({
      title: "Удалить правило?",
      message: "«" + rule.name + "» перестанет участвовать в расчёте.",
      confirmLabel: "Удалить",
      danger: true
    }))) return;
    try {
      await api.delete("/salary/rules/" + rule.id);
      closeRuleForm();
      await loadRules();
    } catch (error) {
      formError(error && error.message ? error.message : "Не удалось удалить правило");
    }
  }

  /* ---------- расчёт ---------- */

  async function calculate() {
    var value = byId("salaryPeriod").value;
    if (!value) return;
    var parts = value.split("-");
    var host = byId("salaryResult");
    host.innerHTML = '<div style="background:#fff;border:1px solid #EBE6E6;border-radius:16px;' +
      'padding:20px;font-size:12.5px;color:#857D7D;font-weight:600">Считаем…</div>';
    try {
      var result = await api.get(
        "/salary/calculate?year=" + Number(parts[0]) + "&month=" + Number(parts[1])
      );
      host.innerHTML = resultHtml(result);
    } catch (error) {
      host.innerHTML = "";
      notify({
        title: "Не удалось рассчитать",
        message: error && error.message ? error.message : ""
      });
    }
  }

  function resultHtml(result) {
    if (!result.rows.length) {
      return '<div style="background:#fff;border:1px solid #EBE6E6;border-radius:16px;' +
        'padding:20px;font-size:12.5px;color:#857D7D;font-weight:600;line-height:1.6">' +
        "За этот период ни одно правило ни на кого не действует.</div>";
    }
    return '<div style="background:#fff;border:1px solid #EBE6E6;border-radius:16px;' +
      'overflow:hidden">' +
      '<div style="padding:17px 20px;border-bottom:1px solid #F0EBEB;display:flex;' +
      'align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap">' +
      '<div style="font-size:13.5px;font-weight:700">Расчёт за ' + result.month + "." +
      result.year + "</div>" +
      '<div style="font-size:12px;color:#857D7D;font-weight:600">Сотрудников: ' +
      result.people + " · к выплате: <b>" + money(result.total) + "</b></div></div>" +
      result.rows.map(function (row) {
        return '<div style="padding:15px 20px;border-bottom:1px solid #F7F4F4">' +
          '<div style="display:flex;align-items:center;justify-content:space-between;gap:12px;' +
          'flex-wrap:wrap"><div style="font-size:13px;font-weight:700">' +
          escapeHtml(row.user_name) + '</div><div style="font-size:13px;font-weight:700">' +
          money(row.payout) + "</div></div>" +
          '<div style="font-size:11px;color:#9B9292;font-weight:600;margin-top:4px">' +
          escapeHtml(row.rules.join(" → ")) + "</div>" +
          '<div style="display:grid;gap:4px;margin-top:8px">' +
          row.lines.map(function (line) {
            return '<div style="font-size:11.5px;color:#6A6161;font-weight:600">· ' +
              escapeHtml(line.explanation) + " = " + money(line.amount) + "</div>";
          }).join("") + "</div>" +
          (Number(row.accrued) < 0
            ? '<div style="font-size:11px;color:#B91414;font-weight:700;margin-top:6px">' +
              "Начислено " + money(row.accrued) + " — к выплате ноль, минус переносится " +
              "долгом в Финансах</div>"
            : "") + "</div>";
      }).join("") + "</div>";
  }

  /* ---------- загрузка и события ---------- */

  async function loadRules() {
    var page = await api.get("/salary/rules");
    state.rules = page.items || [];
    renderRules();
  }

  function bind() {
    byId("salaryRuleCreate").addEventListener("click", function () { openRuleForm(null); });
    byId("salaryModalClose").addEventListener("click", closeRuleForm);
    byId("salaryCancel").addEventListener("click", closeRuleForm);
    byId("salaryModal").addEventListener("click", function (event) {
      if (event.target === byId("salaryModal")) closeRuleForm();
    });
    byId("salarySave").addEventListener("click", function () { saveRule(); });
    byId("salaryDelete").addEventListener("click", function () { deleteRule(); });
    byId("salaryScope").addEventListener("change", renderTarget);
    byId("salaryMode").addEventListener("change", renderModeHint);
    byId("salaryCalculate").addEventListener("click", function () { calculate(); });

    byId("salaryRules").addEventListener("click", function (event) {
      var edit = event.target.closest ? event.target.closest("[data-salary-edit]") : null;
      if (edit) openRuleForm(edit.getAttribute("data-salary-edit"));
    });

    Array.prototype.forEach.call(document.querySelectorAll("[data-salary-add]"), function (button) {
      button.addEventListener("click", function () {
        readComponents();
        state.components.push(newComponent(button.getAttribute("data-salary-add")));
        renderComponents();
      });
    });

    byId("salaryComponents").addEventListener("click", function (event) {
      var target = event.target;
      if (!target.closest) return;
      var remove = target.closest("[data-salary-remove]");
      if (remove) {
        readComponents();
        state.components.splice(Number(remove.getAttribute("data-salary-remove")), 1);
        return renderComponents();
      }
      var tierAdd = target.closest("[data-tier-add]");
      if (tierAdd) {
        readComponents();
        var index = Number(tierAdd.getAttribute("data-tier-add"));
        state.components[index].tiers.push({ up_to: "", percent: "" });
        return renderComponents();
      }
      var tierRemove = target.closest("[data-tier-remove]");
      if (tierRemove) {
        readComponents();
        var parts = tierRemove.getAttribute("data-tier-remove").split(":");
        state.components[Number(parts[0])].tiers.splice(Number(parts[1]), 1);
        return renderComponents();
      }
    });

    // Смена базы у вычета переключает поле «процент»/«сумма», поэтому блок
    // перерисовывается целиком.
    byId("salaryComponents").addEventListener("change", function (event) {
      var select = event.target;
      if (!select.getAttribute || select.getAttribute("data-part") !== "base") return;
      readComponents();
      if (state.components[Number(select.getAttribute("data-index"))].kind === "deduction") {
        renderComponents();
      }
    });
  }

  async function init(user) {
    if (!byId("salaryRules")) return;
    state.user = user;
    if (!hasPermission(user, "salary.view")) {
      var tab = document.querySelector('[data-settings-tab="salary"]');
      if (tab) tab.style.display = "none";
      byId("salaryPanel").innerHTML = "";
      return;
    }
    state.canManage = hasPermission(user, "salary.manage");
    byId("salaryRuleCreate").style.display = state.canManage ? "" : "none";
    var now = window.CelestialTime.today();
    byId("salaryPeriod").value = now.getFullYear() + "-" +
      String(now.getMonth() + 1).padStart(2, "0");
    bind();
    var loaded = await Promise.all([
      api.get("/salary/bases"),
      api.get("/salary/rules")
    ]);
    state.reference = loaded[0];
    state.roles = loaded[0].roles || [];
    state.people = loaded[0].users || [];
    state.rules = loaded[1].items || [];
    renderRules();
  }

  window.CelestialSalary = { init: init, reload: loadRules };
})();
