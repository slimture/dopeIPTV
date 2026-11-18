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
# Where each feature picture shows its feature, per language (site/spots.json).
| ($spots[0] // {}) as $SP
# The legal pages (site/legal.json): their titles, and the languages they are
# written in - Swedish, which governs, and English. Every other language
# shows the English text inside its own header and footer.
| $legal[0] as $L
| "https://dopeiptv.org" as $SITE | "https://github.com/slimture/dopeIPTV" as $REPO
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
           ["close", "donate_close"], ["loading", "donate_loading"], ["paused", "donate_paused"],
           ["apple", "donate_apple"], ["google", "donate_google"], ["card", "donate_card"],
           ["key", "donate_key"], ["keyhow", "donate_key_how"], ["keycopy", "donate_key_copy"],
           ["keycopied", "donate_key_copied"], ["keyopen", "donate_key_open"], ["keywait", "donate_key_wait"],
           ["keynone", "donate_key_none"]][]
          | . as [$k, $key] | " data-\($k)=\"\(t($key) | h)\""] | add)
      + (if $PAYEE == "" then "" else " data-payee=\"\($PAYEE | h)\" data-payeenote=\"\(t("donate_payee_note") | h)\""
             + (if $PAYEE_LOGO == "" then "" else " data-payeelogo=\"\($PAYEE_LOGO | h)\"" end)
             + (if $PAYEE_URL == "" then "" else " data-payeeurl=\"\($PAYEE_URL | h)\"" end) end)
      + (if $MORE_EMAIL == "" then "" else " data-more=\"\(t("donate_more") | h)\" data-moremail=\"\($MORE_EMAIL | h)\"" end)
      + " data-lang=\"\($lang | h)\"></div>\n"
    end;
  # Feedback from the footer's link (nav.js): its words, on every page.
  def feedback_i18n:
    "<div id=\"feedback-i18n\" hidden"
    + ([[["title", "fbs_title"], ["intro", "fbs_intro"], ["topic", "fbs_topic"], ["bug", "fbs_t_bug"],
         ["idea", "fbs_t_idea"], ["like", "fbs_t_like"], ["question", "fbs_t_question"], ["where", "fbs_where"],
         ["app", "fbs_app"], ["site", "fbs_site"], ["ph", "fbs_ph"], ["send", "fbs_send"], ["sent", "fbs_sent"],
         ["failed", "fbs_failed"], ["wait", "fbs_wait"], ["privacy", "fbs_privacy"], ["close", "donate_close"]][]
        | . as [$k, $key] | " data-\($k)=\"\(t($key) | h)\""] | add)
    + "></div>\n";
  def nav($current):
    ($current == "home") as $home
    | (if $home then "" else "/" end) as $base
    | (if $current == "help" then "/help/"
       elif $current == "features" then "/features/"
       elif ($L.pages | index($current)) != null then "/\($current)/"
       else "/" end) as $self
    | [[$base + "#features", t("nav_features"), ""],
       [$base + "#download", t("nav_download"), ""],
       ["/help/", t("nav_help"), "help"]] as $links
    # No GitHub here: a word most visitors do not know, in the one row every
    # one of them reads. The footer has it for whoever looks for the source.
    | def cur($key): $key != "" and $key == $current;
    # The bar's links are only what a visitor leaves the page for: Help, then
    # Donate, gathered at the end with the language and the blue Download
    # button - one group, the logo alone on the other side. The features
    # are further down this same page, where everyone scrolls
    # anyway. The narrow-window menu keeps every link - it is all there is
    # where the row folds away.
    ([$links[] | select(.[2] == "help") | . as [$href, $label, $key]
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
         + "        <summary aria-label=\"\(t("lang_label") | h)\" title=\"\($S.names[$lang] // $lang | h)\">\n"
         + "          <span class=\"langpick-cur\"><span class=\"langpick-globe\">🌐</span><span class=\"langpick-name\">\($lang | ascii_upcase | h)</span></span>\n"
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
      + donate_i18n + feedback_i18n;
  def common:
    {lang: ($lang | h),
     dir: (if ($S.rtl | index($lang)) then "rtl" else "ltr" end),
     locale: ($S.locale[$lang] // "en_US" | h),
     css: ($asset_urls["/style.css"] | h), navjs: ($asset_urls["/nav.js"] | h),
     appjs: ($asset_urls["/app.js"] | h), helpjs: ($asset_urls["/help.js"] | h),
     featjs: ($asset_urls["/features.js"] // "/features.js" | h),
     # Without a hash (an older sync-releases.sh) still the file, not "null".
     eventsjs: ($asset_urls["/events.js"] // "/events.js" | h),
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
        "      <div class=\"dlx\" data-how-more=\"\(t("dl_how_more") | h)\" data-close=\"\(t("lb_close") | h)\">\n"
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
              + "            <a class=\"dlx-how\" href=\"/help/#start-\($grp.id)\" data-how=\"\($grp.id)\">\(t("dl_how") | h)</a>\n"
              + "          </article>\n"] | add)
        + "        </div>\n"
        # The help page's install answers, for the dialog "How to install"
        # opens (app.js): the same text, kept in one place.
        + ([$groups[] | .id as $id | t("help_start_\($id)_q") as $q
            | select($q != "help_start_\($id)_q")
            | "        <template id=\"how-\($id)\" data-title=\"\($q | h)\">\(t("help_start_\($id)_a"))</template>\n"] | add // "")
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
      + "          <small>\(t("donate_paused_note") | h)</small>\n"
      + "        </div>\n"
      + "      </div>\n"
      + "    </div>\n"
      + "  </section>\n\n"
    end;
  # The app's own pictures (tools/site_screenshots.py in its repository):
  # public/screenshots/<lang>/<name>.webp, in the reader's language or else
  # English; $screenshots lists what is there as "<lang>/<name>". A smaller
  # copy of a picture, "<name>-<width>", is offered beside it for phones.
  def pic_lang($name):
    if ($screenshots | index("\($lang)/\($name)")) then $lang
    elif ($screenshots | index("en/\($name)")) then "en"
    else null end;
  # The pictures' own version (sync-releases.sh: their names and sizes),
  # else the release's - a picture replaced gets a new address at once.
  def pics_v($version): ($ARGS.named.pics_v // "") as $p
    | if $p != "" then $p else $version end;
  def pic_src($l; $name; $version):
    "/screenshots/\($l)/\($name).webp?v=\(pics_v($version))";
  def pic_set($l; $name; $small; $full; $version):
    if ($screenshots | index("\($l)/\($name)-\($small)")) then
      "\(pic_src($l; "\($name)-\($small)"; $version)) \($small)w, \(pic_src($l; $name; $version)) \($full)w"
    else "" end;
  # The first picture: the app with a channel playing, in five of its themes,
  # one fading into the next (app.js). Only the first is fetched with the
  # page; the script fetches the others once it starts the slideshow.
  # Without them the drawn mock-up after it stays.
  def heroshow($version):
    [range(1; 6) as $n | "hero-\($n)" as $name | pic_lang($name) as $l
     | select($l != null) | [$l, $name]] as $pics
    | if ($pics | length) == 0 then "" else
        "      <div class=\"heroshow\" data-close=\"\(t("lb_close") | h)\">\n"
        + ($pics | to_entries | map(.key as $i | .value as [$l, $name]
            | pic_set($l; $name; 1280; 2560; $version) as $set
            | if $i == 0 then
                "        <img class=\"hs on\" src=\"\(pic_src($l; $name; $version) | h)\""
                + (if $set != "" then " srcset=\"\($set | h)\" sizes=\"(max-width: 1100px) 94vw, 1040px\"" else "" end)
                + " width=\"2560\" height=\"1600\" alt=\"\(t("hero_alt") | h)\" fetchpriority=\"high\" decoding=\"async\">\n"
              else
                "        <img class=\"hs\" data-src=\"\(pic_src($l; $name; $version) | h)\""
                + (if $set != "" then " data-srcset=\"\($set | h)\" sizes=\"(max-width: 1100px) 94vw, 1040px\"" else "" end)
                + " width=\"2560\" height=\"1600\" alt=\"\" decoding=\"async\">\n"
              end)
          | add)
        + "      </div>\n" end;
  # A feature's picture in the explorer, over its drawn scene; the spot it
  # zooms to and rings (data-x, -y: its centre, -w, -h: its size, all as
  # parts of the picture) - app.js turns them into the picture's style.
  def fx($k; $version):
    "feat-\($k)" as $name
    | pic_lang($name) as $l
    | if $l == null then "" else
        ($SP[$l][$k] // null) as $sp
        | pic_set($l; $name; 960; 1600; $version) as $set
        | "<div class=\"fx-pic\""
          + (if $sp != null then
               " data-x=\"\($sp.x)\" data-y=\"\($sp.y)\" data-w=\"\($sp.w // 0)\" data-h=\"\($sp.h // 0)\""
             else "" end)
          + "><img src=\"\(pic_src($l; $name; $version) | h)\""
          + (if $set != "" then " srcset=\"\($set | h)\" sizes=\"(max-width: 860px) 94vw, 860px\"" else "" end)
          + " width=\"1600\" height=\"1000\" alt=\"\" loading=\"lazy\" decoding=\"async\">"
          + (if $sp != null then "<i class=\"fx-spot\"></i>" else "" end)
          + "</div>"
      end;
  def fx_blocks($version):
    reduce ("c1", "c2", "c3", "mv", "c4", "mine", "tv", "room", "c5", "lf",
            "cast", "subs", "keys", "c6") as $k
      ({}; .["fx_\($k)"] = fx($k; $version));
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
      "featureList": ["Xtream Codes, M3U and Stalker portals", "Enigma2, Tvheadend, Jellyfin, Emby and Plex",
        "Full XMLTV EPG guide", "Live timeshift and catch-up TV", "One-click recording",
        "Multiview — up to nine channels at once", "Built-in mpv player",
        "Mine: continue watching, favourites and a watch-later list",
        "TV mode with a remote, a game controller or the phone",
        "Casting to Chromecast, DLNA smart TVs, Kodi and Apple TV", "Subtitles from OpenSubtitles",
        "Desktop media controls and media keys", "One search across every source",
        "New-episode alerts for followed series", "Trakt and Simkl sync",
        "Your own local video and music files"],
      "license": "\($REPO)/blob/main/LICENSE", "isAccessibleForFree": true,
      "author": {"@id": "\($SITE)/#org"}, "publisher": {"@id": "\($SITE)/#org"},
      "sameAs": [$REPO, "https://www.wikidata.org/wiki/Q142109783"],
      "offers": {"@type": "Offer", "price": "0", "priceCurrency": "USD"}}
     + (if ($screenshots | index("en/hero-1")) then {"screenshot": "\($SITE)/screenshots/en/hero-1.webp"}
        elif ($screenshots | index("en/main")) then {"screenshot": "\($SITE)/screenshots/en/main.webp"} else {} end)) as $app
    | {"@context": "https://schema.org",
       "@graph": [
         {"@type": "Organization", "@id": "\($SITE)/#org", "name": "dopeIPTV",
          "url": "\($SITE)/", "logo": "\($SITE)/favicon.png", "sameAs": [$REPO],
          "parentOrganization": {"@type": "Organization", "name": "Solecraft AB",
                                 "url": "https://solecraft.se/"}},
         {"@type": "WebSite", "@id": "\($SITE)/#website", "name": "dopeIPTV",
          "url": "\($SITE)/", "inLanguage": $lang,
          "publisher": {"@id": "\($SITE)/#org"}},
         $app]}
    | php_json;
  def page_index:
    ($R.version // "0.7.0" | tostring) as $version
    | (if ($R.published_at | truthy) then fmt_date($R.published_at | tostring) else "" end) as $reldate
    | (($R.notes // "") | tostring | render_notes) as $notes
    | dl_total as $total
    | $tpl
    | expand(common + {version: ($version | h), canonical: (url("/"; $lang) | h)};
        ({hreflang: hreflang("/"), jsonld: jsonld($version), nav: nav("home"),
         heroshow: heroshow($version),
         release_tag: ((if ($reldate | truthy) then " · " + ($reldate | h) else " · " + (t("dl_latest") | h) end)
           + (if $total > 0 then " · <span class=\"dl-count\" title=\"\(t("dl_tip_total") | h)\">↓ \(fmt_count($total) | h)</span>" else "" end)
           + (if $notes == "" then "" else
                " · <a class=\"wn-link\" href=\"\($REPO | h)/releases/tag/v\($version | h)\" rel=\"nofollow\" data-wn>\(t("wn_h2") | h)</a>" end)),
         downloads: downloads($version; $reldate),
         donate: donate,
         donate_footer: (if $DONATE == "" then "" else
           "      <a href=\"\($DONATE | h)\" rel=\"nofollow noopener\">\(t("donate_btn") | h)</a>\n" end),
         verify: (if $sums == "1" then "      <p class=\"autonote\">\(t("note_verify"))</p>\n" else "" end),
         # What's new: the notes in a <template>, opened in a dialog from the
         # link beside the version (app.js) - no section of their own.
         whatsnew: (if $notes == "" then "" else
           "  <template id=\"wn-notes\" data-title=\"\(t("wn_h2") | h) · v\($version | h)\""
           + " data-more=\"\(t("wn_full") | h)\" data-close=\"\(t("lb_close") | h)\">\($notes)</template>\n" end)}
         + fx_blocks($version)));

# -- help/index.php --------------------------------------------------------------

  # Every language of the site, each in its own name - the answer to "which
  # languages?" lists them all ("%LANGS%" in its text), in i18n.json's order.
  # "%DONATE%" in an answer is the donate link, which nav.js opens as the
  # dialog, like the heart's.
  def langlist:
    "<ul class=\"langlist\">"
    + ([$S.names | to_entries[] | select(.key as $k | $avail | index($k) != null)
        | "<li lang=\"\(.key | h)\"><bdi>\(.value | h)</bdi></li>"] | join(""))
    + "</ul>";
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
                 | "\($sec)-\($tp)" as $id
                 | "            <details class=\"help-item\" id=\"\($id | h)\">\n"
                   + "              <summary><h3>\($q | h)</h3></summary>\n"
                   + "              <div class=\"help-a\">\(t("help_\($sec)_\($tp)_a") | gsub("%LANGS%"; langlist) | gsub("%DONATE%"; (if $DONATE == "" then "/#donate" else $DONATE | h end)))"
                   # The settings page the answer is about, as the app shows
                   # it in the reader's language (tools/help_screenshots.py
                   # in the app's repository). Inside a closed <details>,
                   # so it loads when the question is opened.
                   + (if ($S.help_shots // [] | index($id)) then
                        "<img class=\"help-shot\" src=\"/help-shots/\($lang | h)/\($id | h).webp?v=\(pics_v($version) | h)\""
                        + " width=\"\($S.help_shot_size[0])\" height=\"\($S.help_shot_size[1])\""
                        + " loading=\"lazy\" decoding=\"async\" alt=\"\($q | h)\">"
                      else "" end)
                   + "</div>\n"
                   + "            </details>\n"] | add // "")
             + "          </section>\n"] | add),
         help_version: (if ($version | truthy) then " " + ($version | h) else "" end)});

# -- /features/ -------------------------------------------------------------------
# Everything the app does, a line each: site/features.json lists the groups
# (an icon each) and their features in order, with the help answer a feature
# links to; the words are fl_g_<group>, fl_<key>_h and fl_<key>_p. Read as
# --slurpfile featfile, and absent (an older sync) as an empty list.

  def page_features:
    ($R.version // "" | tostring) as $version
    | ($ARGS.named.featfile[0] // {groups: []}) as $FL
    | ([$FL.groups[].items | length] | add // 0) as $total
    | "<svg class=\"ic\" viewBox=\"0 0 24 24\" aria-hidden=\"true\" focusable=\"false\">" as $svg
    | $tpl
    | expand(common + {canonical: (url("/features/"; $lang) | h)};
        {hreflang: hreflang("/features/"), nav: nav("features"),
         fl_index: ("          <a href=\"#top\" data-g=\"\" aria-current=\"true\">\(t("fl_all") | h)<span class=\"n\">\($total)</span></a>\n"
           + ([$FL.groups[]
               | "          <a href=\"#\(.k | h)\" data-g=\"\(.k | h)\">\($svg)\(.icon)</svg><span class=\"l\">\(t("fl_g_\(.k)") | h)</span><span class=\"n\">\(.items | length)</span></a>\n"]
              | add // "")),
         fl_groups: ([$FL.groups[] | .k as $g
           | "          <section class=\"fl-group\" id=\"\($g | h)\" data-g=\"\($g | h)\" aria-labelledby=\"\($g | h)-h\">\n"
             + "            <h2 id=\"\($g | h)-h\">\($svg)\(.icon)</svg>\(t("fl_g_\($g)") | h)</h2>\n"
             + "            <ul class=\"fl-list\">\n"
             + ([.items[] | . as [$k, $help]
                 | "              <li class=\"fl-item\" id=\"f-\($k | h)\"><h3>\(t("fl_\($k)_h") | h)</h3><p>\(t("fl_\($k)_p") | h)</p>"
                   + (if $help != "" then
                        "<a class=\"fl-help\" href=\"/help/#\($help | h)\">\(t("fl_help") | h)</a>"
                      else "" end)
                   + "</li>\n"] | add // "")
             + "            </ul>\n"
             + "          </section>\n"] | add // ""),
         fl_version: (if ($version | truthy) then " " + ($version | h) else "" end)});

# -- 404.php --------------------------------------------------------------------

  def page_404: $tpl | expand(common; {nav: nav("")});

# -- the legal pages: /privacy/, /terms/, /donations/, /about/ ----------------
# The text ($body, site/legal/<language>/<page>.html, which sync-releases.sh
# picks by the same rule) is Swedish or English; a page in any other language
# says so in the reader's language and points its canonical at the English.

  def legal_langs: $L.text | keys;
  def legal_hreflang($path):
    ([legal_langs[] as $c
      | "<link rel=\"alternate\" hreflang=\"\($c | h)\" href=\"\(url($path; $c) | h)\">\n"] | add)
    + "<link rel=\"alternate\" hreflang=\"x-default\" href=\"\($SITE + $path | h)\">\n";
  def page_legal:
    (if ($L.text | has($lang)) then $lang else "en" end) as $cl
    | $L.page[$page][$cl] as $P
    | $L.text[$cl] as $X
    | "/\($page)/" as $path
    | $tpl
    | expand(common + {title: ($P.title | h), desc: ($P.desc | h), clang: ($cl | h),
                       canonical: (url($path; $cl) | h)};
        {hreflang: legal_hreflang($path), nav: nav($page),
         tabs: ([$L.pages[] as $p
           | "        <a href=\"/\($p | h)/\"\(if $p == $page then " aria-current=\"page\"" else "" end)>\(t("foot_\($p)") | h)</a>\n"]
           | add),
         note: (if $cl == $lang then "" else
           "      <p class=\"legal-note\">\(t("legal_only_en") | h)</p>\n" end),
         meta: ("<time datetime=\"\($L.updated | h)\">\($X.updated | h)</time> · \($X.governing | h)"
           + " <a href=\"\($path + "?lang=" + $X.link | h)\" hreflang=\"\($X.link | h)\" lang=\"\($X.link | h)\">\($X.other | h)</a>"),
         body: $body});

# -- sitemap.php ------------------------------------------------------------------

  def page_sitemap:
    [["/", (if ($R.published_at | truthy) then (try ($R.published_at | tostring | fromdateiso8601) catch null) else null end)],
     ["/help/", $help_mtime], ["/features/", $help_mtime]] as $pages
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
      # The legal pages, in the languages they are written in.
      + ([$L.pages[] as $p | legal_langs[] as $code
          | "/\($p)/" as $path
          | "  <url>\n"
            + "    <loc>\(url($path; $code) | h)</loc>\n"
            + "    <lastmod>\($L.updated | h)</lastmod>\n"
            + ([legal_langs[] as $alt
                | "    <xhtml:link rel=\"alternate\" hreflang=\"\($alt | h)\" href=\"\(url($path; $alt) | h)\"/>\n"] | add)
            + "    <xhtml:link rel=\"alternate\" hreflang=\"x-default\" href=\"\($SITE + $path | h)\"/>\n"
            + "  </url>\n"] | add)
      + "</urlset>\n";

  if $page == "index" then page_index
  elif $page == "help" then page_help
  elif $page == "features" then page_features
  elif $page == "404" then page_404
  elif $page == "sitemap" then page_sitemap
  elif ($L.pages | index($page)) != null then page_legal
  else error("render.jq: unknown page \($page)") end
