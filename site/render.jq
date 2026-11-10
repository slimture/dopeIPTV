# The site's pages, rendered from site/templates/ - what index.php,
# help/index.php, 404.php, nav.php and sitemap.php used to do on every
# request, done once per sync instead. sync-releases.sh runs it once per page
# and language:
#
#   jq -n -j -f site/render.jq --arg page index --arg lang sv ... (see there)
#
# Every rule is the PHP's, down to the byte: h() is
# htmlspecialchars(ENT_QUOTES), t() falls back to English and then to the
# key, dates and counts follow site/formats.json (what PHP's intl wrote with
# ICU 76.1, FreeBSD's), and the release notes go through the same small
# Markdown subset.
#
# Every `X as $x | ...` starts right after a `|`, `(`, `[`, `;`, `then` or
# `else`: jq 1.8 (FreeBSD's) reads `a and b as $x | c` as
# `(a and b) as $x | c`, jq 1.7 as `a and (b as $x | c)`.
#
# Placeholders in the templates: {{t:key}} a translation as it is (some hold
# markup), {{h:key}} a translation escaped, {{v:name}} a value and {{b:name}}
# a block worked out below.

$site[0] as $S | $en[0] as $E | $tr[0] as $T | $fmt[0] as $F
| ($rel[0] // {}) as $R | ($dl[0] // {}) as $D | $helpfile[0] as $HELP
| "https://iptv.dope.rs" as $SITE | "https://github.com/slimture/dopeIPTV" as $REPO
# The donate button's address: a Stripe Payment Link (donate.stripe.com/...
# or buy.stripe.com/...). nav.js opens the donate dialog over it (paid
# through donate/donate.py); the link itself is what a click does without
# script. Empty: no donate section and no footer link at all.
| "https://donate.stripe.com/bJe4gy7gv4SmdrYgGba7C00" as $DONATE
# Who the money goes to - the company behind dopeIPTV, and the name on the
# donor's card statement: one quiet line at the foot of the donate dialog and
# the footer's copyright (kept small on purpose). Empty: the dialog line goes.
| "Solecraft AB" as $PAYEE
# Its mark beside the payee line (public/solecraft.svg, from solecraft.se).
| "/solecraft-logo.svg" as $PAYEE_LOGO
| "https://solecraft.se" as $PAYEE_URL
# Where a larger gift is arranged (a bank transfer, no card fees): under the
# dialog's amounts. Empty: no such line.
| "info@solecraft.se" as $MORE_EMAIL

# -- the PHP helpers ---------------------------------------------------------

| def h: tostring | gsub("&"; "&amp;") | gsub("\""; "&quot;")
    | gsub("'"; "&#039;") | gsub("<"; "&lt;") | gsub(">"; "&gt;");
  def t($k): $T[$k] // $E[$k] // $k;
  # PHP's idea of an empty value: what `if ($x)` and empty() skip.
  def truthy: . != null and . != false and . != "" and . != "0" and . != 0
    and . != [] and . != {};
  def url($path; $code):
    $SITE + $path + (if $code == "en" then "" else "?lang=" + ($code | @uri) end);
  def expand($v; $b):
    gsub("\\{\\{(?<op>[thvb]):(?<k>[A-Za-z0-9_]+)\\}\\}";
      if .op == "t" then t(.k)
      elif .op == "h" then t(.k) | h
      elif .op == "v" then ($v[.k] // error("render.jq: no value \(.k)"))
      else ($b[.k] // error("render.jq: no block \(.k)")) end);

# -- dates and counts in the reader's own format (fmt_date, fmt_count) -------

  def localdigits($digits):
    ($digits | explode) as $d
    | explode | map(if . >= 48 and . <= 57 then $d[. - 48] else . end) | implode;
  def pad2: tostring | if length < 2 then "0" + . else . end;
  def fmt_date($iso):
    (try ($iso | fromdateiso8601) catch null) as $ts
    | if $ts == null then ""
      elif ($F.formats[$lang] // null) == null then $ts | strftime("%Y-%m-%d")
      else
        $F.formats[$lang] as $f
        | ($ts | gmtime) as $g
        | [$f.date[] as [$kind, $x]
           | if $kind == "lit" then $x
             elif $x == "MMM" then $f.mmm[$g[1]]
             else (if $x == "d" then $g[2] | tostring
                   elif $x == "dd" then $g[2] | pad2
                   elif $x == "M" then $g[1] + 1 | tostring
                   elif $x == "MM" then $g[1] + 1 | pad2
                   else $g[0] | tostring end)
                  | localdigits($f.date_digits)
             end] | add
      end;
  def group($sep; $size2):
    if length <= 3 then .
    else .[:-3] as $head | .[-3:] as $tail
      | [$head | range(length; 0; -$size2) as $e
          | .[(if $e - $size2 < 0 then 0 else $e - $size2 end):$e]]
      | reverse | join($sep) + $sep + $tail
    end;
  def fmt_count($n):
    ($n | tostring) as $s
    | ($F.formats[$lang] // null) as $f
    | if $f == null then $s
      elif ($s | length) >= $f.group_from then $s | group($f.group; $f.group2) | localdigits($f.digits)
      else $s | localdigits($f.digits) end;

# -- the release notes: index.php's render_notes() -----------------------------
# PCRE without the u flag: \s is ASCII whitespace only, so the classes are
# spelled out rather than left to jq's Unicode-wide \s.

  def ws: "[ \\t\\n\\x0B\\f\\r]";
  def rtrim_php: sub("[ \\t\\n\\r\\x00\\x0B]+$"; "");
  def trim_php: sub("^[ \\t\\n\\r\\x00\\x0B]+"; "") | rtrim_php;
  def inline:
    h
    | gsub("`(?<a>[^`]+)`"; "<code>\(.a)</code>")
    | gsub("\\*\\*(?<a>[^*]+)\\*\\*"; "<strong>\(.a)</strong>")
    | gsub("\\[(?<a>[^\\]]+)\\]\\((?<u>https?:&#0*39;?[^) \\t\\n\\x0B\\f\\r]*|https?://[^) \\t\\n\\x0B\\f\\r]+)\\)";
        (.u | gsub("&(?<e>amp|quot|#0*39|lt|gt);";
                   {"amp": "&", "quot": "\"", "lt": "<", "gt": ">"}[.e] // "'")) as $href
        | if ($href | test("^https?://"; "i"))
          then "<a href=\"\($href | h)\" rel=\"noopener\">\(.a)</a>"
          else "[\(.a)](\(.u))" end);
  def flush:
    if (.para | length) > 0
    then .out += "<p>" + (.para | join(" ") | inline) + "</p>\n" | .para = []
    else . end;
  def close_list:
    if .in_list then .out += "</ul>\n" | .in_list = false else . end;
  # preg_replace()'s reading of its replacement: $n, ${n} and \n are groups.
  # The pattern has none of its own, so group 0 is the "</li>\n" it matched.
  def php_replacement:
    gsub("\\$\\{(?<n>[0-9][0-9]?)\\}|\\$(?<m>[0-9][0-9]?)|\\\\(?<k>[0-9][0-9]?)";
      if ((.n // .m // .k) | tonumber) == 0 then "</li>\n" else "" end);
  def render_notes:
    reduce splits("\r\n|\n|\r|\u000b|\u000c|\u0085") as $line
      ({out: "", in_list: false, para: []};
       ($line | rtrim_php) as $t
       | if $t == "" then flush | close_list
         elif ($t | test("^##" + ws + "+dopeIPTV(?![A-Za-z0-9_])"; "i")) then .
         elif ($t | test("^###" + ws + "+")) then
           ($t | capture("^###" + ws + "+(?<x>.*)$").x) as $x
           | flush | close_list | .out += "<h3>" + ($x | inline) + "</h3>\n"
         elif ($t | test("^>")) then
           ($t | capture("^>" + ws + "?(?<x>.*)$").x) as $x
           | flush | close_list | .out += "<blockquote>" + ($x | inline) + "</blockquote>\n"
         elif ($t | test("^[-*]" + ws + "+")) then
           ($t | capture("^[-*]" + ws + "+(?<x>.*)$").x) as $x
           | flush
           | (if .in_list then . else .out += "<ul>\n" | .in_list = true end)
           | .out += "<li>" + ($x | inline) + "</li>\n"
         elif .in_list and ($line | test("^" + ws + "+[^ \\t\\n\\x0B\\f\\r]")) then
           (" " + ($t | trim_php | inline) + "</li>\n" | php_replacement) as $r
           | .out |= (if endswith("</li>\n") then .[:-6] + $r else . end)
         else .para += [$t | trim_php] end)
    | flush | close_list | .out;

# -- shared pieces ---------------------------------------------------------------

  def hreflang($path):
    ([$avail[] as $c
      | "<link rel=\"alternate\" hreflang=\"\($c | h)\" href=\"\(url($path; $c) | h)\">\n"]
     | add)
    + "<link rel=\"alternate\" hreflang=\"x-default\" href=\"\($SITE + $path | h)\">\n";
  # The donate dialog's words, for nav.js on every page (data, not script).
  def donate_i18n:
    if $DONATE == "" then "" else
      "<div id=\"donate-i18n\" hidden"
      + ([[["title", "donate_eyebrow"], ["sub", "donate_modal_sub"], ["other", "donate_other"], ["othershort", "donate_other_short"], ["currency", "donate_currency"],
           ["cont", "donate_continue"], ["email", "donate_email"], ["pay", "donate_pay"],
           ["busy", "donate_busy"], ["lock", "donate_lock"], ["back", "donate_back"],
           ["thanks", "donate_thanks"], ["receipt", "donate_receipt"], ["failed", "donate_failed"],
           ["emailmissing", "donate_email_missing"], ["range", "donate_range"],
           ["close", "donate_close"], ["loading", "donate_loading"],
           ["apple", "donate_apple"], ["google", "donate_google"], ["card", "donate_card"]][]
          | . as [$k, $key] | " data-\($k)=\"\(t($key) | h)\""] | add)
      + (if $PAYEE == "" then "" else " data-payee=\"\($PAYEE | h)\" data-payeenote=\"\(t("donate_payee_note") | h)\""
             + (if $PAYEE_LOGO == "" then "" else " data-payeelogo=\"\($PAYEE_LOGO | h)\"" end)
             + (if $PAYEE_URL == "" then "" else " data-payeeurl=\"\($PAYEE_URL | h)\"" end) end)
      + (if $MORE_EMAIL == "" then "" else " data-more=\"\(t("donate_more") | h)\" data-moremail=\"\($MORE_EMAIL | h)\"" end)
      + " data-lang=\"\($lang | h)\"></div>\n"
    end;
  def nav($current):
    ($current == "home") as $home
    | (if $home then "" else "/" end) as $base
    | (if $current == "help" then "/help/" else "/" end) as $self
    | [[$base + "#features", t("nav_features"), ""],
       [$base + "#shots", t("nav_screenshots"), ""],
       [$base + "#download", t("nav_download"), ""],
       ["/help/", t("nav_help"), "help"],
       [$REPO, t("nav_github"), ""]] as $links
    | def cur($key): $key != "" and $key == $current;
    ([$links[] | . as [$href, $label, $key]
      | "      <a class=\"navlink\" href=\"\($href | h)\"\(if cur($key) then " aria-current=\"page\"" else "" end)>\($label | h)</a>\n"]
     | add) as $navlinks
    | ([$links[] | . as [$href, $label, $key]
      | "          <a role=\"menuitem\" class=\"navmenu-item\(if cur($key) then " is-current" else "" end)\" href=\"\($href | h)\"\(if cur($key) then " aria-current=\"page\"" else "" end)>\($label | h)</a>\n"]
     | add) as $menuitems
    # Donate: a quiet link beside the others (nav.js opens the dialog; the
    # address is Stripe's own page for when it cannot).
    | (if $DONATE == "" then "" else
         "      <a class=\"navlink navdonate\" href=\"\($DONATE | h)\" rel=\"nofollow noopener\"><svg class=\"heart\" viewBox=\"0 0 24 24\" aria-hidden=\"true\" focusable=\"false\"><path d=\"M12 21s-7.5-4.6-9.6-9.2C.9 8.4 3 4.5 6.9 4.5c2.1 0 3.6 1.1 5.1 3 1.5-1.9 3-3 5.1-3 3.9 0 6 3.9 4.5 7.3C19.5 16.4 12 21 12 21z\"/></svg>\(t("donate_btn") | h)</a>\n" end) as $navdonate
    | (if $DONATE == "" then "" else
         "          <a role=\"menuitem\" class=\"navmenu-item navdonate\" href=\"\($DONATE | h)\" rel=\"nofollow noopener\"><svg class=\"heart\" viewBox=\"0 0 24 24\" aria-hidden=\"true\" focusable=\"false\"><path d=\"M12 21s-7.5-4.6-9.6-9.2C.9 8.4 3 4.5 6.9 4.5c2.1 0 3.6 1.1 5.1 3 1.5-1.9 3-3 5.1-3 3.9 0 6 3.9 4.5 7.3C19.5 16.4 12 21 12 21z\"/></svg>\(t("donate_btn") | h)</a>\n" end) as $menudonate
    # ...and on a phone, where the links fold into the menu, a heart of its
    # own in the header, one tap from anywhere.
    | (if $DONATE == "" then "" else
         "      <a class=\"navheart\" href=\"\($DONATE | h)\" rel=\"nofollow noopener\" aria-label=\"\(t("donate_btn") | h)\" title=\"\(t("donate_btn") | h)\"><svg viewBox=\"0 0 24 24\" aria-hidden=\"true\" focusable=\"false\"><path d=\"M12 21s-7.5-4.6-9.6-9.2C.9 8.4 3 4.5 6.9 4.5c2.1 0 3.6 1.1 5.1 3 1.5-1.9 3-3 5.1-3 3.9 0 6 3.9 4.5 7.3C19.5 16.4 12 21 12 21z\"/></svg></a>\n" end) as $navheart
    | (if ($avail | length) > 1 then
         "      <details class=\"langpick\" id=\"langPick\" name=\"hdrmenu\">\n"
         + "        <summary aria-label=\"\(t("lang_label") | h)\">\n"
         + "          <span class=\"langpick-cur\"><span class=\"langpick-globe\">🌐</span><span class=\"langpick-name\">\($S.names[$lang] // $lang | h)</span></span>\n"
         + "          <span class=\"langpick-caret\" aria-hidden=\"true\">▾</span>\n"
         + "        </summary>\n"
         + "        <div class=\"langpick-menu\" role=\"menu\">\n"
         + ([$avail[] as $c
             | "          <a role=\"menuitem\" class=\"langpick-item\(if $c == $lang then " is-current" else "" end)\" href=\"\($self + "?lang=" + ($c | @uri) | h)\"\(if $c == $lang then " aria-current=\"true\"" else "" end)>\($S.names[$c] // $c | h)</a>\n"]
            | add)
         + "        </div>\n"
         + "      </details>\n"
       else "" end) as $langpick
    | ($navtpl
       | expand({brand_href: (if $home then "#top" else "/" end), nav_base: $base};
                {navlinks: ($navlinks + $navdonate + $navheart), langpick: $langpick,
                 menuitems: ($menuitems + $menudonate)}))
      + donate_i18n;
  def common:
    {lang: ($lang | h),
     dir: (if ($S.rtl | index($lang)) then "rtl" else "ltr" end),
     locale: ($S.locale[$lang] // "en_US" | h),
     css: ($asset_urls["/style.css"] | h), navjs: ($asset_urls["/nav.js"] | h),
     appjs: ($asset_urls["/app.js"] | h), helpjs: ($asset_urls["/help.js"] | h),
     year: $year};

# -- index.php ----------------------------------------------------------------

  def dl_meta($name):
    ($name | ascii_downcase) as $n
    | ($n | contains("arm") or contains("aarch64")) as $arm
    | (if $arm then t("arch_arm") else t("arch_x86") end) as $cpu
    | (if $arm then t("arch_apple_arm") else t("arch_apple_intel") end) as $mac
    | (if $arm then "ARM64" else "x64" end) as $code
    | if $n | endswith(".dmg") then
        {os: "macOS", icon: "🍎", title: t("dl_t_dmg"), fmt: t("dl_f_dmg"), arch: $mac, rank: (if $arm then 10 else 12 end), rec: $arm,
         short: ".dmg", cpu: (if $arm then "Apple Silicon" else "Intel" end), req: (if $arm then "macOS 15+" else "macOS 14+" end)}
      elif $n | endswith(".pkg") then
        {os: "macOS", icon: "🍎", title: t("dl_t_pkg"), fmt: t("dl_f_pkg"), arch: $mac, rank: (if $arm then 20 else 22 end), rec: false,
         short: ".pkg", cpu: (if $arm then "Apple Silicon" else "Intel" end)}
      elif ($n | endswith(".exe")) or ($n | endswith(".msi")) then
        {os: "Windows", icon: "🪟", title: t("dl_t_exe"), fmt: t("dl_f_exe"), arch: $cpu, rank: (if $arm then 12 else 10 end), rec: ($arm | not),
         short: (if $n | endswith(".msi") then ".msi" else ".exe" end), cpu: $code}
      elif ($n | endswith(".zip")) and ($n | contains("win")) then
        {os: "Windows", icon: "🪟", title: t("dl_t_winzip"), fmt: t("dl_f_winzip"), arch: $cpu, rank: (if $arm then 22 else 20 end), rec: ($arm | not),
         short: ".zip", cpu: $code}
      elif $n | endswith(".appimage") then
        {os: "Linux", icon: "🐧", title: t("dl_t_appimage"), fmt: t("dl_f_appimage"), arch: $cpu, rank: (if $arm then 12 else 10 end), rec: ($arm | not),
         short: "AppImage", cpu: $code}
      elif $n | endswith(".deb") then
        {os: "Linux", icon: "🐧", title: t("dl_t_deb"), fmt: t("dl_f_deb"), arch: $cpu, rank: (if $arm then 22 else 20 end), rec: false,
         short: ".deb", cpu: $code}
      elif $n | endswith(".rpm") then
        {os: "Linux", icon: "🐧", title: t("dl_t_rpm"), fmt: t("dl_f_rpm"), arch: $cpu, rank: 30, rec: false,
         short: ".rpm", cpu: $code}
      elif $n | endswith(".flatpak") then
        {os: "Linux", icon: "📦", title: t("dl_t_flatpak"), fmt: t("dl_f_flatpak"), arch: t("arch_universal"), rank: 40, rec: false,
         short: "Flatpak", cpu: ""}
      else {os: "Other", icon: "📦", title: $name, fmt: "", arch: "", rank: 99, rec: false, short: $name, cpu: ""}
      end;
  def dl_n($name): ($D[$name] // 0) | if type == "number" then floor else (tonumber? // 0) end;
  def dl_total: [$D[] | if type == "number" then floor else (tonumber? // 0) end] | add // 0;
  # One file's own bits: its size as GitHub reported it, the counted
  # address, and the checksum shown on hover.
  def dl_size: ((.sub // "") | tostring
      | (capture("(?<s>[0-9.]+[ \\t\\n\\x0B\\f\\r]?(?:KB|MB|GB|B))[ \\t\\n\\x0B\\f\\r]*$"; "i").s // ""));
  def dl_href: ((.name // "" | tostring) as $name
      | (if $name != "" then "/dl.php?f=" + ($name | @uri) else (.url // "#") end));
  # Everything about a file that has no room on the page, for its tooltip.
  def dl_title:
    (._m as $m
     | dl_n(.name // "" | tostring) as $n
     | [$m.title, $m.arch, dl_size,
        (if $n > 0 then "↓ \(fmt_count($n)) \(t("dl_tip_row"))" else empty end),
        (if (.sha256 | truthy) then "SHA-256: \(.sha256)" else empty end)]
     | map(select(type == "string" and . != "")) | join(" · ")
     | " title=\"\(h)\"");
  def dl_icon:
    "<svg class=\"dlx-svg\" viewBox=\"0 0 24 24\" aria-hidden=\"true\" focusable=\"false\"><path d=\"M12 4v12M6 11l6 6 6-6M5 20h14\"/></svg>";
  def dl_github:
    "      <div class=\"dls\">\n"
    + "        <a class=\"dl\" href=\"\($REPO | h)/releases/latest\" rel=\"nofollow\">\n"
    + "          <span class=\"meta\"><span class=\"name\">\(t("dl_all_name") | h)</span><span class=\"sub\">\(t("dl_all_sub") | h)</span></span>\n"
    + "          <span class=\"go\">\(t("dl_open") | h)</span>\n"
    + "        </a>\n"
    + "      </div>\n";
  # A card per system, side by side, each with one thing to press - on a
  # Mac two, since a browser cannot tell Apple Silicon from Intel - and its
  # other formats as a line of small links under it, in plain sight.
  # app.js marks the visitor's own system; without it the three are equal.
  def dl_alt:
    "<a href=\"\(dl_href | h)\" rel=\"nofollow\"\(dl_title)>\(._m.short | h)\(if ._m.cpu != "" then " <span>\(._m.cpu | h)</span>" else "" end)</a>";
  def downloads($version; $reldate):
    ($R.assets // []) as $assets
    | ($assets | map(. + {_m: dl_meta(.name // "" | tostring)})) as $A
    | {"Linux": "linux", "macOS": "macos", "Windows": "windows"} as $ids
    | [("Linux", "macOS", "Windows") as $os
       | ($A | map(select(._m.os == $os)) | sort_by(._m.rank)) as $g
       | select($g | length > 0)
       | ($g | map(select(._m.short == ".dmg"))) as $dmg
       | (if $os == "macOS" and ($dmg | length) > 0 then $dmg
          else ($g | map(select(._m.rec)) | .[:1]) end) as $hero
       | (if ($hero | length) == 0 then $g[:1] else $hero end) as $hero
       | [$hero[] | .name] as $heronames
       | {os: $os, id: $ids[$os], g: $g, hero: $hero,
          rest: ($g | map(select(.name as $nm | $heronames | any(. == $nm) | not)))}] as $groups
    | (t("dl_go") | sub("[[:space:]]*[→←][[:space:]]*$"; "")) as $btn
    | if ($groups | length) == 0 then dl_github else
        "      <div class=\"dlx\">\n"
        + "        <div class=\"dlx-cards\">\n"
        + ([$groups[] | . as $grp
            | (($grp.hero | length) > 1) as $many
            | $grp.hero[0]._m as $m
            | "          <article class=\"dlx-card\" data-os=\"\($grp.id)\">\n"
              + "            <span class=\"badge dlx-yours\">\(t("dl_yours") | h)</span>\n"
              + "            <span class=\"dlx-ico\" aria-hidden=\"true\">\($grp.g[0]._m.icon | h)</span>\n"
              + "            <h3>\($grp.os | h)</h3>\n"
              + "            <p class=\"dlx-sub\">\($m.fmt)</p>\n"
              + "            <div class=\"dlx-btns\">\n"
              + ([$grp.hero | to_entries[] | .key as $k | .value
                  | "              <a class=\"dlx-big\(if $k > 0 then " alt" else "" end)\" href=\"\(dl_href | h)\" rel=\"nofollow\"\(dl_title)>\(dl_icon)<span>\(if $many then (._m.cpu | h) else ($btn | h) end)</span>\(if $many and (._m.req // "") != "" then "<small>\(._m.req | h)</small>" else "" end)</a>\n"] | add)
              + "            </div>\n"
              + "            <p class=\"dlx-meta\">\(if $many then (t("dl_which_mac") | h) else ([$m.title, $m.arch, ($grp.hero[0] | dl_size)] | map(select(. != "")) | map(h) | join(" · ")) end)</p>\n"
              + (if ($grp.rest | length) > 0 then
                   "            <p class=\"dlx-alt\"><span>\(t("dl_other") | h)</span>\([$grp.rest[] | dl_alt] | join(""))</p>\n"
                 else "" end)
              + "            <a class=\"dlx-how\" href=\"/help/#start-\($grp.id)\">\(t("dl_how") | h)</a>\n"
              + "          </article>\n"] | add)
        + "        </div>\n"
        + "        <p class=\"dlx-gh\"><a href=\"\($REPO | h)/releases/latest\" rel=\"nofollow\">\(t("dl_all_name") | h)</a></p>\n"
        + "      </div>\n"
      end;
  def donate:
    if $DONATE == "" then "" else
      "  <section id=\"donate\" class=\"pt0\">\n"
      + "    <div class=\"wrap\">\n"
      + "      <div class=\"donate\">\n"
      + "        <div class=\"donate-fx\" aria-hidden=\"true\"><svg class=\"donate-big\" viewBox=\"0 0 24 24\" focusable=\"false\"><path d=\"M12 21s-7.5-4.6-9.6-9.2C.9 8.4 3 4.5 6.9 4.5c2.1 0 3.6 1.1 5.1 3 1.5-1.9 3-3 5.1-3 3.9 0 6 3.9 4.5 7.3C19.5 16.4 12 21 12 21z\"/></svg><i></i><i></i><i></i><i></i><i></i><i></i></div>\n"
      + "        <div class=\"donate-text\">\n"
      + "          <h2>\(t("donate_h2") | h)</h2>\n"
      + "          <p>\(t("donate_p") | h)</p>\n"
      + "          <p class=\"donate-short\">\(t("donate_p_short") | h)</p>\n"
      + "        </div>\n"
      + "        <div class=\"donate-act\">\n"
      + "          <a class=\"btn primary donate-btn\" href=\"\($DONATE | h)\" rel=\"nofollow noopener\"><svg class=\"donate-heart\" viewBox=\"0 0 24 24\" aria-hidden=\"true\" focusable=\"false\"><path d=\"M12 21s-7.5-4.6-9.6-9.2C.9 8.4 3 4.5 6.9 4.5c2.1 0 3.6 1.1 5.1 3 1.5-1.9 3-3 5.1-3 3.9 0 6 3.9 4.5 7.3C19.5 16.4 12 21 12 21z\"/></svg>\(t("donate_btn") | h)</a>\n"
      + "          <small>\(t("donate_note") | h)</small>\n"
      + "        </div>\n"
      + "      </div>\n"
      + "    </div>\n"
      + "  </section>\n\n"
    end;
  def shots:
    [["main.png", "shot_main_alt", "shot_main_t", "shot_main_c"],
     ["epg.png", "shot_epg_alt", "shot_epg_t", "shot_epg_c"],
     ["timeshift.png", "shot_ts_alt", "shot_ts_t", "shot_ts_c"],
     ["recordings.png", "shot_rec_alt", "shot_rec_t", "shot_rec_c"]]
    | map(. as [$file, $alt, $title, $cap]
      | "        <div class=\"shot\">\n"
        + (if ($screenshots | index($file)) then
             "          <img src=\"/screenshots/\($file | h)\" alt=\"\(t($alt) | h)\" width=\"1280\" height=\"800\" loading=\"lazy\">\n"
           else "          <div class=\"ph\">\(t("shot_ph") | h) · \(t($title))</div>\n" end)
        + "          <div class=\"cap\"><b>\(t($title))</b> — \(t($cap))</div>\n"
        + "        </div>\n")
    | add;
  # json_encode(..., JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE), which
  # still writes U+2028 and U+2029 escaped.
  def php_json: tojson | gsub(" "; "\\u2028") | gsub(" "; "\\u2029");
  def jsonld($version):
    ({"@type": "SoftwareApplication", "@id": "\($SITE)/#app", "name": "dopeIPTV",
      "alternateName": "dopeiptv",
      "operatingSystem": "Linux, macOS, Windows",
      "applicationCategory": "MultimediaApplication",
      "applicationSubCategory": "IPTV player", "description": t("meta_desc"),
      "softwareVersion": $version, "url": "\($SITE)/",
      "downloadUrl": "\($SITE)/#download", "installUrl": "\($SITE)/#download",
      "featureList": ["Xtream Codes and M3U playlists", "Full XMLTV EPG guide",
        "Live timeshift and catch-up TV", "One-click recording",
        "Multiview — up to nine channels at once", "Built-in mpv player",
        "Casting to Chromecast and DLNA smart TVs", "Subtitles from OpenSubtitles",
        "Desktop media controls (MPRIS) and media keys",
        "New-episode alerts for followed series", "Trakt sync",
        "Your own local video and music files"],
      "license": "\($REPO)/blob/main/LICENSE", "isAccessibleForFree": true,
      "author": {"@id": "\($SITE)/#org"}, "publisher": {"@id": "\($SITE)/#org"},
      "sameAs": [$REPO],
      "offers": {"@type": "Offer", "price": "0", "priceCurrency": "USD"}}
     + (if ($screenshots | index("main.png")) then {"screenshot": "\($SITE)/screenshots/main.png"} else {} end)) as $app
    | {"@context": "https://schema.org",
       "@graph": [
         {"@type": "Organization", "@id": "\($SITE)/#org", "name": "dopeIPTV",
          "url": "\($SITE)/", "logo": "\($SITE)/favicon.png", "sameAs": [$REPO],
          "parentOrganization": {"@type": "Organization", "name": "Solecraft AB",
                                 "url": "https://solecraft.se/"}},
         {"@type": "WebSite", "@id": "\($SITE)/#website", "name": "dopeIPTV",
          "url": "\($SITE)/", "inLanguage": $lang,
          "publisher": {"@id": "\($SITE)/#org"}},
         $app,
         {"@type": "FAQPage", "@id": "\($SITE)/#faq",
          "mainEntity": [range(1; 11) as $i
            | {"@type": "Question", "name": t("faq_q\($i)"),
               "acceptedAnswer": {"@type": "Answer", "text": t("faq_a\($i)")}}]}]}
    | php_json;
  def page_index:
    ($R.version // "0.7.0" | tostring) as $version
    | (if ($R.published_at | truthy) then fmt_date($R.published_at | tostring) else "" end) as $reldate
    | (($R.notes // "") | tostring | render_notes) as $notes
    | dl_total as $total
    | $tpl
    | expand(common + {version: ($version | h), canonical: (url("/"; $lang) | h)};
        {hreflang: hreflang("/"), jsonld: jsonld($version), nav: nav("home"),
         shots: shots,
         release_tag: ((if ($reldate | truthy) then " · " + ($reldate | h) else " · " + (t("dl_latest") | h) end)
           + (if $total > 0 then " · <span class=\"dl-count\" title=\"\(t("dl_tip_total") | h)\">↓ \(fmt_count($total) | h)</span>" else "" end)),
         downloads: downloads($version; $reldate),
         donate: donate,
         donate_footer: (if $DONATE == "" then "" else
           "      <a href=\"\($DONATE | h)\" rel=\"nofollow noopener\">\(t("donate_btn") | h)</a>\n" end),
         verify: (if $sums == "1" then "      <p class=\"autonote\">\(t("note_verify"))</p>\n" else "" end),
         whatsnew: (if $notes == "" then "" else
           "  <section id=\"whats-new\" class=\"pt0\">\n"
           + "    <div class=\"wrap\">\n"
           + "      <div class=\"sec-head\">\n"
           + "        <h2>\(t("wn_h2") | h)</h2>\n"
           + "        <p>v\($version | h)\(if ($reldate | truthy) then " · " + ($reldate | h) else "" end) · \(t("wn_intro") | h)</p>\n"
           + "      </div>\n"
           + "      <details class=\"notes-wrap\">\n"
           + "        <summary>\(t("wn_open") | h)</summary>\n"
           + "        <div class=\"notes\">\($notes)</div>\n"
           + "      </details>\n"
           + "      <p class=\"autonote\"><a href=\"\($REPO | h)/releases/tag/v\($version | h)\" rel=\"nofollow\">\(t("wn_full") | h)</a> · <a href=\"\($REPO | h)/blob/main/CHANGELOG.md\" rel=\"nofollow\">\(t("wn_changelog") | h)</a></p>\n"
           + "    </div>\n"
           + "  </section>\n" end)});

# -- help/index.php --------------------------------------------------------------

  def page_help:
    ($R.version // "" | tostring) as $version
    | $tpl
    | expand(common + {canonical: (url("/help/"; $lang) | h)};
        {hreflang: hreflang("/help/"), nav: nav("help"),
         toc: ([$HELP | to_entries[] | .key as $sec
           | "          <a href=\"#\($sec | h)\" data-toc=\"\($sec | h)\">\(t("help_\($sec)_title") | h)</a>\n"] | add),
         sections: ([$HELP | to_entries[] | .key as $sec
           | "          <section class=\"help-sec\" id=\"\($sec | h)\" data-sec=\"\($sec | h)\">\n"
             + "            <h2>\(t("help_\($sec)_title") | h)</h2>\n"
             + ([.value[] as $tp
                 | t("help_\($sec)_\($tp)_q") as $q
                 | select($q != "help_\($sec)_\($tp)_q")
                 | "            <details class=\"help-item\" id=\"\("\($sec)-\($tp)" | h)\">\n"
                   + "              <summary><h3>\($q | h)</h3></summary>\n"
                   + "              <div class=\"help-a\">\(t("help_\($sec)_\($tp)_a"))</div>\n"
                   + "            </details>\n"] | add // "")
             + "          </section>\n"] | add),
         help_version: (if ($version | truthy) then " " + ($version | h) else "" end)});

# -- 404.php --------------------------------------------------------------------

  def page_404: $tpl | expand(common; {nav: nav("")});

# -- sitemap.php ------------------------------------------------------------------

  def page_sitemap:
    [["/", (if ($R.published_at | truthy) then (try ($R.published_at | tostring | fromdateiso8601) catch null) else null end)],
     ["/help/", $help_mtime]] as $pages
    | "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
      + "<urlset xmlns=\"http://www.sitemaps.org/schemas/sitemap/0.9\" xmlns:xhtml=\"http://www.w3.org/1999/xhtml\">\n"
      + ([$pages[] as [$path, $ts] | $avail[] as $code
          | "  <url>\n"
            + "    <loc>\(url($path; $code) | h)</loc>\n"
            + (if $ts != null and $ts != 0 then "    <lastmod>\($ts | strftime("%Y-%m-%d"))</lastmod>\n" else "" end)
            + ([$avail[] as $alt
                | "    <xhtml:link rel=\"alternate\" hreflang=\"\($alt | h)\" href=\"\(url($path; $alt) | h)\"/>\n"] | add)
            + "    <xhtml:link rel=\"alternate\" hreflang=\"x-default\" href=\"\($SITE + $path | h)\"/>\n"
            + "  </url>\n"] | add)
      + "</urlset>\n";

  if $page == "index" then page_index
  elif $page == "help" then page_help
  elif $page == "404" then page_404
  elif $page == "sitemap" then page_sitemap
  else error("render.jq: unknown page \($page)") end
