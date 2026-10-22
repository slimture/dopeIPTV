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
    | $navtpl
    | expand({brand_href: (if $home then "#top" else "/" end), nav_base: $base};
             {navlinks: $navlinks, langpick: $langpick, menuitems: $menuitems});
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
    | if $n | endswith(".dmg") then
        {os: "macOS", icon: "🍎", title: t("dl_t_dmg"), fmt: t("dl_f_dmg"), arch: $mac, rank: (if $arm then 10 else 12 end), rec: $arm}
      elif $n | endswith(".pkg") then
        {os: "macOS", icon: "🍎", title: t("dl_t_pkg"), fmt: t("dl_f_pkg"), arch: $mac, rank: (if $arm then 20 else 22 end), rec: false}
      elif ($n | endswith(".exe")) or ($n | endswith(".msi")) then
        {os: "Windows", icon: "🪟", title: t("dl_t_exe"), fmt: t("dl_f_exe"), arch: $cpu, rank: (if $arm then 12 else 10 end), rec: ($arm | not)}
      elif ($n | endswith(".zip")) and ($n | contains("win")) then
        {os: "Windows", icon: "🪟", title: t("dl_t_winzip"), fmt: t("dl_f_winzip"), arch: $cpu, rank: (if $arm then 22 else 20 end), rec: ($arm | not)}
      elif $n | endswith(".appimage") then
        {os: "Linux", icon: "🐧", title: t("dl_t_appimage"), fmt: t("dl_f_appimage"), arch: $cpu, rank: (if $arm then 12 else 10 end), rec: ($arm | not)}
      elif $n | endswith(".deb") then
        {os: "Linux", icon: "🐧", title: t("dl_t_deb"), fmt: t("dl_f_deb"), arch: $cpu, rank: (if $arm then 22 else 20 end), rec: false}
      elif $n | endswith(".rpm") then
        {os: "Linux", icon: "🐧", title: t("dl_t_rpm"), fmt: t("dl_f_rpm"), arch: $cpu, rank: 30, rec: false}
      elif $n | endswith(".flatpak") then
        {os: "Linux", icon: "📦", title: t("dl_t_flatpak"), fmt: t("dl_f_flatpak"), arch: t("arch_universal"), rank: 40, rec: false}
      else {os: "Other", icon: "📦", title: $name, fmt: "", arch: "", rank: 99, rec: false}
      end;
  def dl_n($name): ($D[$name] // 0) | if type == "number" then floor else (tonumber? // 0) end;
  def dl_total: [$D[] | if type == "number" then floor else (tonumber? // 0) end] | add // 0;
  def downloads($version; $reldate):
    ($R.assets // []) as $assets
    | if ($assets | truthy) then
        ($assets | map(. + {_m: dl_meta(.name // "" | tostring)})) as $A
        | {"Linux": t("os_help_linux"), "macOS": t("os_help_macos"), "Windows": t("os_help_windows")} as $help
        | {"Linux": t("os_install_linux"), "macOS": t("os_install_macos"), "Windows": t("os_install_windows")} as $install
        | [("Linux", "macOS", "Windows", "Other") as $os
           | ($A | map(select(._m.os == $os)) | sort_by(._m.rank)) as $g
           | select($g | length > 0)
           | "      <div class=\"dl-group\">\n"
             + "        <div class=\"dl-group-head\">\n"
             + "          <span class=\"dl-os-ico\">\($g[0]._m.icon | h)</span>\n"
             + "          <h3>\($os | h)</h3>\n"
             + "        </div>\n"
             + (if ($help[$os] | truthy) then "        <p class=\"dl-guide\">\($help[$os])</p>\n" else "" end)
             + "        <div class=\"dls\">\n"
             + ([$g[] | ._m as $m
                 | ((.sub // "") | tostring
                    | (capture("(?<s>[0-9.]+[ \\t\\n\\x0B\\f\\r]?(?:KB|MB|GB|B))[ \\t\\n\\x0B\\f\\r]*$"; "i").s // "")) as $size
                 | (.name // "" | tostring) as $name
                 | (if $name != "" then "/dl.php?f=" + ($name | @uri) else (.url // "#") end) as $href
                 | dl_n($name) as $n
                 | "          <a class=\"dl\(if $m.rec then " rec" else "" end)\" href=\"\($href | h)\" rel=\"nofollow\"\(if (.sha256 | truthy) then " title=\"SHA-256: \(.sha256 | h)\"" else "" end)>\n"
                   + "            <span class=\"meta\">\n"
                   + "              <span class=\"name\">\($m.title | h)\(if $m.rec then " <span class=\"badge\">\(t("dl_recommended") | h)</span>" else "" end)</span>\n"
                   + "              <span class=\"sub\"><span class=\"arch\">\($m.arch | h)</span>\(if ($m.fmt | truthy) then " · " + $m.fmt else "" end)\(if ($size | truthy) then " · " + ($size | h) else "" end)\(if $n > 0 then " · <span class=\"dl-count\" title=\"\(t("dl_tip_row") | h)\">↓ \(fmt_count($n) | h)</span>" else "" end)</span>\n"
                   + "            </span>\n"
                   + "            <span class=\"go\">\(t("dl_go") | h)</span>\n"
                   + "          </a>\n"] | add)
             + "        </div>\n"
             + (if ($install[$os] | truthy) then "        <p class=\"autonote\">\($install[$os])</p>\n" else "" end)
             + "      </div>\n"] | add
      else
        "      <div class=\"dls\">\n"
        + "        <a class=\"dl\" href=\"\($REPO | h)/releases/latest\" rel=\"nofollow\">\n"
        + "          <span class=\"meta\"><span class=\"name\">\(t("dl_all_name") | h)</span><span class=\"sub\">\(t("dl_all_sub") | h)</span></span>\n"
        + "          <span class=\"go\">\(t("dl_open") | h)</span>\n"
        + "        </a>\n"
        + "      </div>\n"
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
          "url": "\($SITE)/", "logo": "\($SITE)/favicon.png", "sameAs": [$REPO]},
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
         verify: (if $sums == "1" then "      <p class=\"autonote\">\(t("note_verify"))</p>\n" else "" end),
         whatsnew: (if $notes == "" then "" else
           "  <section id=\"whats-new\" class=\"pt0\">\n"
           + "    <div class=\"wrap\">\n"
           + "      <div class=\"sec-head\">\n"
           + "        <span class=\"eyebrow\">\(t("wn_eyebrow") | h) · v\($version | h)\(if ($reldate | truthy) then " · " + ($reldate | h) else "" end)</span>\n"
           + "        <h2>\(t("wn_h2") | h)</h2>\n"
           + "        <p>\(t("wn_intro") | h)</p>\n"
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
                 | "            <article class=\"help-item\" id=\"\("\($sec)-\($tp)" | h)\">\n"
                   + "              <h3><a href=\"#\("\($sec)-\($tp)" | h)\">\($q | h)</a></h3>\n"
                   + "              <div class=\"help-a\">\(t("help_\($sec)_\($tp)_a"))</div>\n"
                   + "            </article>\n"] | add // "")
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
