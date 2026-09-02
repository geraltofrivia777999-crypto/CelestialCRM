/*
 * Единый вид выпадающих списков.
 *
 * Само поле `<select>` мы давно стилизуем: рамка, скругление, своя стрелка.
 * А вот список, который браузер открывает по клику, не стилизуется вообще —
 * это окно операционной системы. На фоне CRM он выглядит чужим: системный
 * шрифт, синяя подсветка, острые углы.
 *
 * Поэтому здесь заменяется только список, а не поле. Ни одна страница не
 * переписывается: `<select>` остаётся на своём месте со своими классами,
 * размерами и значением, и весь существующий код продолжает читать `.value`
 * и слушать `change`. Мы лишь перехватываем открытие и рисуем свой список.
 *
 * Список с поиском: у офферов и пользователей их сотни, и пролистывать такой
 * перечень глазами бесполезно.
 */
(function () {
  "use strict";

  // Ниже этого числа поиск только мешает: строка ввода занимает место, а
  // выбрать проще глазами.
  var SEARCH_FROM = 8;
  var MAX_HEIGHT = 320;

  var open = null;

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function skip(select) {
    return !select || select.disabled || select.multiple || select.size > 1 ||
      select.hasAttribute("data-native-select");
  }

  /* Второй строкой варианта можно показать техническую подпись — `data-hint`
     на <option>. Так рекламный кабинет остаётся в списке под своим именем, но
     ищется ещё и по ID: в комментариях и в чужих ссылках имени часто нет, а
     ID есть, и без этого кабинет было не найти. */
  function optionRow(option) {
    return {
      value: option.value,
      label: option.textContent,
      hint: option.getAttribute("data-hint") || "",
      disabled: option.disabled
    };
  }

  function entries(select) {
    /* Плоский список строк панели: заголовки групп идут вперемешку с
       вариантами, потому что рисуются они в одном столбце. */
    var rows = [];
    Array.prototype.forEach.call(select.children, function (node) {
      if (node.tagName === "OPTGROUP") {
        rows.push({ group: true, label: node.label });
        Array.prototype.forEach.call(node.children, function (option) {
          rows.push(optionRow(option));
        });
        return;
      }
      if (node.tagName !== "OPTION") return;
      rows.push(optionRow(node));
    });
    return rows;
  }

  function panelHtml(rows, current, searchable) {
    return (searchable
      ? '<div class="csel-search"><input type="text" class="csel-input" ' +
        'placeholder="Поиск" autocomplete="off"></div>'
      : "") +
      '<div class="csel-list" role="listbox">' +
      rows.map(function (row, index) {
        if (row.group) {
          return '<div class="csel-group" data-index="' + index + '">' +
            escapeHtml(row.label) + "</div>";
        }
        var on = String(row.value) === String(current);
        return '<button type="button" class="csel-option' + (on ? " csel-option--on" : "") +
          '" role="option" aria-selected="' + on + '" data-index="' + index + '"' +
          (row.disabled ? " disabled" : "") +
          (row.hint ? ' data-hint="' + escapeHtml(row.hint) + '"' : "") + ">" +
          '<span class="csel-option-text">' + escapeHtml(row.label) +
          (row.hint
            ? '<span class="csel-option-hint">' + escapeHtml(row.hint) + "</span>"
            : "") + "</span>" +
          (on
            ? '<svg class="csel-tick" width="15" height="15" viewBox="0 0 24 24" ' +
              'fill="none" aria-hidden="true"><path d="m5 12.5 4.5 4.5L19 7.5" ' +
              'stroke="currentColor" stroke-width="2.4" stroke-linecap="round" ' +
              'stroke-linejoin="round"/></svg>'
            : "") + "</button>";
      }).join("") +
      '<div class="csel-empty" hidden>Ничего не найдено</div></div>';
  }

  function place(panel, select) {
    /* Панель прибита к окну, а не к полю: формы лежат в модалках с
       прокруткой, и панель внутри них обрезалась бы краем окна. */
    var box = select.getBoundingClientRect();
    var width = Math.max(box.width, 220);
    panel.style.width = Math.min(width, window.innerWidth - 16) + "px";
    panel.style.left = Math.max(
      8, Math.min(box.left, window.innerWidth - panel.offsetWidth - 8)
    ) + "px";
    var below = window.innerHeight - box.bottom - 10;
    var above = box.top - 10;
    var height = Math.min(MAX_HEIGHT, Math.max(below, above));
    panel.style.maxHeight = height + "px";
    // Вниз, если снизу помещается; иначе вверх — но не за верхний край.
    if (below >= Math.min(panel.scrollHeight, MAX_HEIGHT) || below >= above) {
      panel.style.top = box.bottom + 6 + "px";
    } else {
      panel.style.top = Math.max(8, box.top - panel.offsetHeight - 6) + "px";
    }
  }

  function keepScrollFocus(element) {
    /* Фокус без прыжка страницы. Safari умеет игнорировать preventScroll и
       доскролливать документ уже после того, как мы вернули позицию: позицию
       панелей он в этот момент считает по потоку документа, а панель лежит
       в конце body — внизу страницы. Поэтому позицию возвращаем ещё пару
       раз: следующим кадром и чуть позже. */
    var left = window.pageXOffset;
    var top = window.pageYOffset;
    var restore = function () {
      if (window.pageXOffset !== left || window.pageYOffset !== top) {
        window.scrollTo(left, top);
      }
    };
    try {
      element.focus({ preventScroll: true });
    } catch (_error) {
      // Старые браузеры не знают preventScroll — позицию восстановим ниже.
      element.focus();
    }
    restore();
    if (window.requestAnimationFrame) window.requestAnimationFrame(restore);
    window.setTimeout(restore, 50);
  }

  function scrollOptionIntoView(option) {
    var list = option && option.parentElement;
    if (!list) return;
    var top = option.offsetTop;
    var bottom = top + option.offsetHeight;
    if (top < list.scrollTop) list.scrollTop = top;
    if (bottom > list.scrollTop + list.clientHeight) {
      list.scrollTop = bottom - list.clientHeight;
    }
  }

  function close(restoreFocus) {
    if (!open) return;
    var select = open.select;
    open.panel.remove();
    select.removeAttribute("aria-expanded");
    open = null;
    if (restoreFocus) keepScrollFocus(select);
  }

  function choose(select, value) {
    if (String(select.value) === String(value)) return close(true);
    select.value = value;
    close(true);
    // Событие с всплытием: страницы слушают его на контейнере формы.
    select.dispatchEvent(new Event("input", { bubbles: true }));
    select.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function visibleOptions() {
    return Array.prototype.filter.call(
      open.panel.querySelectorAll(".csel-option"),
      function (node) { return node.offsetParent !== null && !node.disabled; }
    );
  }

  function move(step) {
    var options = visibleOptions();
    if (!options.length) return;
    var index = options.indexOf(open.panel.querySelector(".csel-option--active"));
    var next = index < 0
      ? (step > 0 ? 0 : options.length - 1)
      : Math.max(0, Math.min(options.length - 1, index + step));
    options.forEach(function (node) { node.classList.remove("csel-option--active"); });
    options[next].classList.add("csel-option--active");
    scrollOptionIntoView(options[next]);
  }

  function filter(query) {
    var clean = String(query || "").trim().toLowerCase();
    var shown = 0;
    Array.prototype.forEach.call(open.panel.querySelectorAll(".csel-option"), function (node) {
      var haystack = (node.textContent + " " + (node.getAttribute("data-hint") || ""))
        .toLowerCase();
      var hit = !clean || haystack.indexOf(clean) >= 0;
      node.hidden = !hit;
      if (hit) shown += 1;
    });
    // Заголовок группы без единого видимого варианта — просто мусор в списке.
    Array.prototype.forEach.call(open.panel.querySelectorAll(".csel-group"), function (head) {
      var node = head.nextElementSibling;
      var any = false;
      while (node && !node.classList.contains("csel-group")) {
        if (node.classList.contains("csel-option") && !node.hidden) any = true;
        node = node.nextElementSibling;
      }
      head.hidden = !any;
    });
    open.panel.querySelector(".csel-empty").hidden = shown > 0;
  }

  function openPanel(select) {
    if (open && open.select === select) return close(true);
    close(false);
    var rows = entries(select);
    if (!rows.length) return;
    var searchable = rows.filter(function (row) { return !row.group; }).length >= SEARCH_FROM;

    var panel = document.createElement("div");
    panel.className = "csel-panel";
    panel.innerHTML = panelHtml(rows, select.value, searchable);
    document.body.appendChild(panel);
    open = { select: select, panel: panel, rows: rows };
    select.setAttribute("aria-expanded", "true");
    place(panel, select);

    var active = panel.querySelector(".csel-option--on") ||
      panel.querySelector(".csel-option");
    if (active) {
      active.classList.add("csel-option--active");
      scrollOptionIntoView(active);
    }

    panel.addEventListener("mousedown", function (event) { event.preventDefault(); });
    panel.addEventListener("click", function (event) {
      var option = event.target.closest(".csel-option");
      if (!option || option.disabled) return;
      choose(select, rows[Number(option.getAttribute("data-index"))].value);
    });
    panel.addEventListener("mousemove", function (event) {
      var option = event.target.closest(".csel-option");
      if (!option) return;
      Array.prototype.forEach.call(panel.querySelectorAll(".csel-option"), function (node) {
        node.classList.remove("csel-option--active");
      });
      option.classList.add("csel-option--active");
    });

    var search = panel.querySelector(".csel-input");
    if (search) {
      search.addEventListener("input", function () { filter(search.value); });
      // Без защиты Safari на фокусе инпута уезжает всей страницей.
      keepScrollFocus(search);
    }
  }

  function currentValue() {
    var active = open.panel.querySelector(".csel-option--active");
    if (!active || active.hidden) active = visibleOptions()[0];
    return active ? open.rows[Number(active.getAttribute("data-index"))].value : null;
  }

  document.addEventListener("mousedown", function (event) {
    if (open && !open.panel.contains(event.target) && event.target !== open.select) {
      close(false);
    }
    var select = event.target && event.target.closest
      ? event.target.closest("select")
      : null;
    if (skip(select)) return;
    // Отменяем родной список, но фокус ставим сами — без него поле не
    // подсвечивается и клавиатура после закрытия панели никуда не попадает.
    event.preventDefault();
    keepScrollFocus(select);
    openPanel(select);
  }, true);

  document.addEventListener("keydown", function (event) {
    if (open) {
      if (event.key === "Escape") { event.preventDefault(); return close(true); }
      if (event.key === "ArrowDown") { event.preventDefault(); return move(1); }
      if (event.key === "ArrowUp") { event.preventDefault(); return move(-1); }
      if (event.key === "Enter" || event.key === "Tab") {
        var value = currentValue();
        event.preventDefault();
        if (value !== null) return choose(open.select, value);
        return close(true);
      }
      return;
    }
    var select = event.target;
    if (skip(select) || select.tagName !== "SELECT") return;
    if (event.key === "Enter" || event.key === " " ||
      (event.key === "ArrowDown" && event.altKey)) {
      event.preventDefault();
      openPanel(select);
    }
  }, true);

  /*
   * Панель прибита к окну, поэтому при прокрутке страницы её надо двигать
   * следом. Именно двигать, а не закрывать: закрытие по любому событию
   * прокрутки убивало панель сразу после открытия — список сам прокручивает
   * себя к выбранному пункту, и это тоже прокрутка.
   *
   * Прокрутка внутри панели нас не касается, а если поле уехало за край
   * экрана — панели больше не к чему прижиматься, и она закрывается.
   */
  window.addEventListener("scroll", function (event) {
    if (!open || open.panel.contains(event.target)) return;
    var box = open.select.getBoundingClientRect();
    if (box.bottom < 0 || box.top > window.innerHeight) return close(false);
    place(open.panel, open.select);
  }, true);
  window.addEventListener("resize", function () { close(false); });
})();
