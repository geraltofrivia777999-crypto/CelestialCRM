"""Country normalization for the offer GEO column.

Keitaro hands the GEO back in whatever shape the tracker was set up with:
an alpha-2 code (`IN`), an alpha-3 code (`IND`), or a full country name
(`INDIA`, `Индия`). The CRM shows the alpha-2 code everywhere, so every
value passes through `normalize_geo()` — both on sync and on read, which
means rows synced before this existed also render as codes.
"""

# alpha-2, alpha-3, English name, *aliases
_COUNTRIES: tuple[tuple[str, ...], ...] = (
    ("AD", "AND", "ANDORRA"),
    ("AE", "ARE", "UNITED ARAB EMIRATES", "UAE", "ОАЭ", "ЭМИРАТЫ"),
    ("AF", "AFG", "AFGHANISTAN", "АФГАНИСТАН"),
    ("AG", "ATG", "ANTIGUA AND BARBUDA", "ANTIGUA", "АНТИГУА И БАРБУДА"),
    ("AL", "ALB", "ALBANIA", "АЛБАНИЯ"),
    ("AM", "ARM", "ARMENIA", "АРМЕНИЯ"),
    ("AO", "AGO", "ANGOLA", "АНГОЛА"),
    # Meta принимает Антарктиду страной в таргетинге — без неё её не выбрать в связке.
    ("AQ", "ATA", "ANTARCTICA", "АНТАРКТИДА", "АНТАРКТИКА"),
    ("AR", "ARG", "ARGENTINA", "АРГЕНТИНА"),
    ("AT", "AUT", "AUSTRIA", "АВСТРИЯ"),
    ("AU", "AUS", "AUSTRALIA", "АВСТРАЛИЯ"),
    ("AZ", "AZE", "AZERBAIJAN", "АЗЕРБАЙДЖАН"),
    ("BA", "BIH", "BOSNIA AND HERZEGOVINA", "BOSNIA", "БОСНИЯ И ГЕРЦЕГОВИНА"),
    ("BB", "BRB", "BARBADOS", "БАРБАДОС"),
    ("BD", "BGD", "BANGLADESH", "БАНГЛАДЕШ"),
    ("BE", "BEL", "BELGIUM", "БЕЛЬГИЯ"),
    ("BF", "BFA", "BURKINA FASO"),
    ("BG", "BGR", "BULGARIA", "БОЛГАРИЯ"),
    ("BH", "BHR", "BAHRAIN", "БАХРЕЙН"),
    ("BJ", "BEN", "BENIN"),
    ("BO", "BOL", "BOLIVIA", "БОЛИВИЯ"),
    ("BR", "BRA", "BRAZIL", "БРАЗИЛИЯ"),
    ("BS", "BHS", "BAHAMAS", "THE BAHAMAS", "БАГАМЫ"),
    ("BY", "BLR", "BELARUS", "БЕЛАРУСЬ", "БЕЛОРУССИЯ"),
    ("BZ", "BLZ", "BELIZE", "БЕЛИЗ"),
    ("CA", "CAN", "CANADA", "КАНАДА"),
    ("CD", "COD", "CONGO (KINSHASA)", "DR CONGO", "DEMOCRATIC REPUBLIC OF THE CONGO"),
    ("CG", "COG", "CONGO"),
    ("CH", "CHE", "SWITZERLAND", "ШВЕЙЦАРИЯ"),
    ("CI", "CIV", "COTE D'IVOIRE", "IVORY COAST"),
    ("CL", "CHL", "CHILE", "ЧИЛИ"),
    ("CM", "CMR", "CAMEROON", "КАМЕРУН"),
    ("CN", "CHN", "CHINA", "КИТАЙ"),
    ("CO", "COL", "COLOMBIA", "КОЛУМБИЯ"),
    ("CR", "CRI", "COSTA RICA", "КОСТА-РИКА"),
    ("CU", "CUB", "CUBA", "КУБА"),
    ("CY", "CYP", "CYPRUS", "КИПР"),
    ("CZ", "CZE", "CZECHIA", "CZECH REPUBLIC", "ЧЕХИЯ"),
    ("DE", "DEU", "GERMANY", "ГЕРМАНИЯ"),
    ("DK", "DNK", "DENMARK", "ДАНИЯ"),
    ("DM", "DMA", "DOMINICA", "ДОМИНИКА"),
    ("DO", "DOM", "DOMINICAN REPUBLIC", "ДОМИНИКАНА"),
    ("DZ", "DZA", "ALGERIA", "АЛЖИР"),
    ("EC", "ECU", "ECUADOR", "ЭКВАДОР"),
    ("EE", "EST", "ESTONIA", "ЭСТОНИЯ"),
    ("EG", "EGY", "EGYPT", "ЕГИПЕТ"),
    ("ES", "ESP", "SPAIN", "ИСПАНИЯ"),
    ("ET", "ETH", "ETHIOPIA", "ЭФИОПИЯ"),
    ("FI", "FIN", "FINLAND", "ФИНЛЯНДИЯ"),
    ("FR", "FRA", "FRANCE", "ФРАНЦИЯ"),
    ("GB", "GBR", "UNITED KINGDOM", "UK", "GREAT BRITAIN", "ENGLAND", "ВЕЛИКОБРИТАНИЯ"),
    ("GD", "GRD", "GRENADA", "ГРЕНАДА"),
    ("GE", "GEO", "GEORGIA", "ГРУЗИЯ"),
    ("GH", "GHA", "GHANA", "ГАНА"),
    ("GR", "GRC", "GREECE", "ГРЕЦИЯ"),
    ("GT", "GTM", "GUATEMALA", "ГВАТЕМАЛА"),
    ("GY", "GUY", "GUYANA", "ГАЙАНА"),
    ("HK", "HKG", "HONG KONG", "ГОНКОНГ"),
    ("HN", "HND", "HONDURAS", "ГОНДУРАС"),
    ("HR", "HRV", "CROATIA", "ХОРВАТИЯ"),
    ("HT", "HTI", "HAITI", "ГАИТИ"),
    ("HU", "HUN", "HUNGARY", "ВЕНГРИЯ"),
    ("ID", "IDN", "INDONESIA", "ИНДОНЕЗИЯ"),
    ("IE", "IRL", "IRELAND", "ИРЛАНДИЯ"),
    ("IL", "ISR", "ISRAEL", "ИЗРАИЛЬ"),
    ("IN", "IND", "INDIA", "ИНДИЯ"),
    ("IQ", "IRQ", "IRAQ", "ИРАК"),
    ("IR", "IRN", "IRAN", "ИРАН"),
    ("IS", "ISL", "ICELAND", "ИСЛАНДИЯ"),
    ("IT", "ITA", "ITALY", "ИТАЛИЯ"),
    ("JM", "JAM", "JAMAICA", "ЯМАЙКА"),
    ("JO", "JOR", "JORDAN", "ИОРДАНИЯ"),
    ("JP", "JPN", "JAPAN", "ЯПОНИЯ"),
    ("KE", "KEN", "KENYA", "КЕНИЯ"),
    ("KG", "KGZ", "KYRGYZSTAN", "КИРГИЗИЯ", "КЫРГЫЗСТАН"),
    ("KH", "KHM", "CAMBODIA", "КАМБОДЖА"),
    ("KN", "KNA", "SAINT KITTS AND NEVIS", "ST KITTS AND NEVIS"),
    ("KR", "KOR", "SOUTH KOREA", "KOREA", "REPUBLIC OF KOREA", "ЮЖНАЯ КОРЕЯ"),
    ("KW", "KWT", "KUWAIT", "КУВЕЙТ"),
    ("KZ", "KAZ", "KAZAKHSTAN", "КАЗАХСТАН"),
    ("LB", "LBN", "LEBANON", "ЛИВАН"),
    ("LC", "LCA", "SAINT LUCIA", "ST LUCIA", "СЕНТ-ЛЮСИЯ"),
    ("LK", "LKA", "SRI LANKA", "ШРИ-ЛАНКА"),
    ("LT", "LTU", "LITHUANIA", "ЛИТВА"),
    ("LU", "LUX", "LUXEMBOURG", "ЛЮКСЕМБУРГ"),
    ("LV", "LVA", "LATVIA", "ЛАТВИЯ"),
    ("LY", "LBY", "LIBYA", "ЛИВИЯ"),
    ("MA", "MAR", "MOROCCO", "МАРОККО"),
    ("MD", "MDA", "MOLDOVA", "МОЛДОВА", "МОЛДАВИЯ"),
    ("ME", "MNE", "MONTENEGRO", "ЧЕРНОГОРИЯ"),
    ("MK", "MKD", "NORTH MACEDONIA", "MACEDONIA", "МАКЕДОНИЯ"),
    ("ML", "MLI", "MALI"),
    ("MM", "MMR", "MYANMAR", "BURMA", "МЬЯНМА"),
    ("MN", "MNG", "MONGOLIA", "МОНГОЛИЯ"),
    ("MT", "MLT", "MALTA", "МАЛЬТА"),
    ("MX", "MEX", "MEXICO", "МЕКСИКА"),
    ("MY", "MYS", "MALAYSIA", "МАЛАЙЗИЯ"),
    ("MZ", "MOZ", "MOZAMBIQUE", "МОЗАМБИК"),
    ("NG", "NGA", "NIGERIA", "НИГЕРИЯ"),
    ("NI", "NIC", "NICARAGUA", "НИКАРАГУА"),
    ("NL", "NLD", "NETHERLANDS", "HOLLAND", "НИДЕРЛАНДЫ", "ГОЛЛАНДИЯ"),
    ("NO", "NOR", "NORWAY", "НОРВЕГИЯ"),
    ("NP", "NPL", "NEPAL", "НЕПАЛ"),
    ("NZ", "NZL", "NEW ZEALAND", "НОВАЯ ЗЕЛАНДИЯ"),
    ("OM", "OMN", "OMAN", "ОМАН"),
    ("PA", "PAN", "PANAMA", "ПАНАМА"),
    ("PE", "PER", "PERU", "ПЕРУ"),
    ("PH", "PHL", "PHILIPPINES", "ФИЛИППИНЫ"),
    ("PK", "PAK", "PAKISTAN", "ПАКИСТАН"),
    ("PL", "POL", "POLAND", "ПОЛЬША"),
    ("PT", "PRT", "PORTUGAL", "ПОРТУГАЛИЯ"),
    ("PY", "PRY", "PARAGUAY", "ПАРАГВАЙ"),
    ("QA", "QAT", "QATAR", "КАТАР"),
    ("RO", "ROU", "ROMANIA", "РУМЫНИЯ"),
    ("RS", "SRB", "SERBIA", "СЕРБИЯ"),
    ("RU", "RUS", "RUSSIA", "RUSSIAN FEDERATION", "РОССИЯ"),
    ("SA", "SAU", "SAUDI ARABIA", "КСА", "САУДОВСКАЯ АРАВИЯ"),
    ("SE", "SWE", "SWEDEN", "ШВЕЦИЯ"),
    ("SG", "SGP", "SINGAPORE", "СИНГАПУР"),
    ("SI", "SVN", "SLOVENIA", "СЛОВЕНИЯ"),
    ("SK", "SVK", "SLOVAKIA", "СЛОВАКИЯ"),
    ("SN", "SEN", "SENEGAL", "СЕНЕГАЛ"),
    ("SR", "SUR", "SURINAME", "СУРИНАМ"),
    ("SV", "SLV", "EL SALVADOR", "САЛЬВАДОР"),
    ("SY", "SYR", "SYRIA", "СИРИЯ"),
    ("TH", "THA", "THAILAND", "ТАИЛАНД"),
    ("TJ", "TJK", "TAJIKISTAN", "ТАДЖИКИСТАН"),
    ("TM", "TKM", "TURKMENISTAN", "ТУРКМЕНИСТАН"),
    ("TN", "TUN", "TUNISIA", "ТУНИС"),
    ("TR", "TUR", "TURKEY", "TURKIYE", "ТУРЦИЯ"),
    ("TT", "TTO", "TRINIDAD AND TOBAGO", "TRINIDAD", "ТРИНИДАД И ТОБАГО"),
    ("TW", "TWN", "TAIWAN", "ТАЙВАНЬ"),
    ("TZ", "TZA", "TANZANIA", "ТАНЗАНИЯ"),
    ("UA", "UKR", "UKRAINE", "УКРАИНА"),
    ("UG", "UGA", "UGANDA", "УГАНДА"),
    ("US", "USA", "UNITED STATES", "UNITED STATES OF AMERICA", "USA", "США"),
    ("UY", "URY", "URUGUAY", "УРУГВАЙ"),
    ("UZ", "UZB", "UZBEKISTAN", "УЗБЕКИСТАН"),
    ("VC", "VCT", "SAINT VINCENT AND THE GRENADINES", "ST VINCENT"),
    ("VE", "VEN", "VENEZUELA", "ВЕНЕСУЭЛА"),
    ("VN", "VNM", "VIETNAM", "VIET NAM", "ВЬЕТНАМ"),
    ("YE", "YEM", "YEMEN", "ЙЕМЕН"),
    ("ZA", "ZAF", "SOUTH AFRICA", "ЮАР"),
    ("ZM", "ZMB", "ZAMBIA", "ЗАМБИЯ"),
    ("ZW", "ZWE", "ZIMBABWE", "ЗИМБАБВЕ"),
)

_ALPHA2 = {row[0] for row in _COUNTRIES}
_BY_TOKEN: dict[str, str] = {}
for _row in _COUNTRIES:
    for _token in _row[1:]:
        _BY_TOKEN[_token] = _row[0]

# Keitaro writes one of these when an offer is not tied to a single country.
_WORLDWIDE = {"WW", "WORLDWIDE", "WORLD WIDE", "GLOBAL", "ALL", "ANY", "МИР", "ВЕСЬ МИР"}


def countries() -> list[dict[str, str]]:
    """Все известные страны кодом и человеческим названием.

    Справочник тиров показывает страны списком, и брать его больше неоткуда:
    Keitaro отдаёт только те гео, что уже встретились в офферах, а тир стране
    назначают заранее — до первой открутки.
    """
    return [
        {"code": row[0], "name": row[2].title()}
        for row in sorted(_COUNTRIES, key=lambda item: item[2])
    ]


def country_options() -> list[dict[str, str]]:
    """Страны для форм: код, английское название и русское, если оно известно.

    Русские названия лежат среди алиасов нормализации — отдельного словаря под
    них заводить не надо, а искать страну в форме команда будет по-русски.
    """
    rows = []
    for row in sorted(_COUNTRIES, key=lambda item: item[2]):
        russian = next(
            (token for token in row[3:] if any("А" <= char <= "я" for char in token)),
            "",
        )
        # Аббревиатуры оставляем как есть: «Сша» и «Оаэ» выглядят опечаткой.
        pretty = russian if len(russian) <= 4 else russian.capitalize()
        rows.append({"code": row[0], "name": row[2].title(), "ru": pretty})
    return rows


def normalize_geo(value: object) -> str | None:
    """Return the alpha-2 code for `value`, or a trimmed fallback we can show."""
    if isinstance(value, list | tuple):
        for entry in value:
            code = normalize_geo(entry)
            if code:
                return code
        return None
    token = " ".join(str(value or "").split()).upper()
    if not token:
        return None
    if token in _WORLDWIDE:
        return "WW"
    if token in _ALPHA2:
        return token
    if token in _BY_TOKEN:
        return _BY_TOKEN[token]
    # "DE,AT" or "DE / AT": a multi-geo offer, first resolvable country wins.
    for separator in (",", "/", ";", "|"):
        if separator in token:
            for part in token.split(separator):
                code = normalize_geo(part)
                if code:
                    return code
    # Значения, сохранённые до того, как страна попала в таблицу, лежат в базе
    # обрезанными («ANTIGUA AND» от «ANTIGUA AND BARBUDA»). Однозначный префикс
    # разрешается в код, поэтому старые строки чинятся на чтении, без ресинка.
    if len(token) >= 5:
        matches = {code for name, code in _BY_TOKEN.items() if name.startswith(token)}
        if len(matches) == 1:
            return matches.pop()
    # Unknown, but truncating keeps it storable and still tells the user something.
    return token[:12]
