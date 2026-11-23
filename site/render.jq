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
# A tip for the app (the support page, /support/), on dopeIPTV's own Stripe
# account: given in the tip dialog on the page (nav.js: Stripe's card form,
# Apple Pay and Google Pay, donate/donate.py) once SUPPORT_TIPS=on, else
# through a Stripe Payment Link (SUPPORT_TIP_URL). They are the server's,
# not git's: sync-releases.sh reads them from /usr/local/etc/iptv-site.conf
# and passes them here. Without either the tip says "coming soon" and
# links nowhere. What the page suggests is set beside them
# (SUPPORT_TIP_HINT). A tip is for the app and gives nothing: no key, no
# monthly support, no other way to give (November 2026).
| (($ARGS.named.tips // "") == "on") as $TIPS_ON
| ($ARGS.named.tip_url // "") as $TIP_URL
| (($ARGS.named.tip_hint // "") | if . == "" then "100 kr" else . end) as $TIP_HINT
# The tip is invited to - the header's link and heart, the front page's
# band, the footer's link - only once it is set up: until then the support
# page is there (its terms, "coming soon"), but nothing on the site asks for
# money that cannot be taken. Every one of them goes to the support page -
# as the app's heart does; its tip button opens the tip dialog there
# (data-tip-dialog).
| ($TIPS_ON or $TIP_URL != "") as $SUPPORT_ON
# The company behind dopeIPTV: in every footer and on the support page, as
# Stripe's review and the Swedish E-Commerce Act ask.
| "Solecraft AB" as $COMPANY
| "559601-3176" as $ORGNR
| "SE559601317601" as $VAT
# The footer names the town; the full address is on the About and contact
# page (linked beside it), the support page, the privacy policy and the terms.
| "Örebro" as $TOWN
| "Mogatan 21, 702 13 Örebro" as $ADDRESS
| "info@solecraft.se" as $EMAIL
# The tip dialog's foot: who is paid (the logo links to the company), and
# whom to write to for more than its largest amount.
| "/solecraft-logo.svg" as $PAYEE_LOGO
| "https://solecraft.se" as $PAYEE_URL

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
    # A day alone ("2026-11-21", the transparency page's) is that day's start.
    (try ($iso | if test("^[0-9]{4}-[0-9]{2}-[0-9]{2}$") then . + "T00:00:00Z" else . end | fromdateiso8601)
     catch null) as $ts
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
  # The tip dialog's words (nav.js), on every page while it is on. The
  # amounts, the currencies and Stripe's publishable key come from the
  # service (/pay/config).
  def tip_i18n:
    if $TIPS_ON | not then "" else
      "<div id=\"tip-i18n\" hidden"
      + ([[["title", "support_btn"], ["sub", "tip_sub"], ["other", "tip_other"], ["othershort", "tip_other_short"],
           ["currency", "tip_currency"], ["cont", "tip_continue"], ["email", "tip_email"], ["pay", "tip_pay"],
           ["busy", "tip_busy"], ["lock", "support_stripe"], ["back", "tip_back"],
           ["thanks", "support_thanks"], ["receipt", "tip_receipt"], ["failed", "tip_failed"],
           ["emailmissing", "tip_email_missing"], ["range", "tip_range"],
           ["close", "tip_close"], ["loading", "support_loading"],
           ["apple", "tip_apple"], ["google", "tip_google"], ["card", "tip_card"],
           ["payeenote", "tip_payee_note"], ["more", "tip_more"]][]
          | . as [$k, $key] | " data-\($k)=\"\(t($key) | h)\""] | add)
      + " data-payee=\"\($COMPANY | h)\" data-payeelogo=\"\($PAYEE_LOGO | h)\" data-payeeurl=\"\($PAYEE_URL | h)\""
      + " data-moremail=\"\($EMAIL | h)\""
      + " data-lang=\"\($lang | h)\"></div>\n"
    end;
  # Feedback from the footer's link (nav.js): its words, on every page.
  def feedback_i18n:
    "<div id=\"feedback-i18n\" hidden"
    + ([[["title", "fbs_title"], ["intro", "fbs_intro"], ["topic", "fbs_topic"], ["bug", "fbs_t_bug"],
         ["idea", "fbs_t_idea"], ["like", "fbs_t_like"], ["question", "fbs_t_question"], ["where", "fbs_where"],
         ["app", "fbs_app"], ["site", "fbs_site"], ["ph", "fbs_ph"], ["send", "fbs_send"], ["sent", "fbs_sent"],
         ["failed", "fbs_failed"], ["wait", "fbs_wait"], ["privacy", "fbs_privacy"], ["close", "tip_close"]][]
        | . as [$k, $key] | " data-\($k)=\"\(t($key) | h)\""] | add)
    + "></div>\n";
  def nav($current):
    ($current == "home") as $home
    | (if $home then "" else "/" end) as $base
    | (if $current == "help" then "/help/"
       elif $current == "features" then "/features/"
       elif $current == "transparency" then "/transparency/"
       elif ($L.pages | index($current)) != null then "/\($current)/"
       else "/" end) as $self
    | [[$base + "#features", t("nav_features"), "features"],
       [$base + "#download", t("nav_download"), ""],
       ["/help/", t("nav_help"), "help"]] as $links
    # No GitHub here: a word most visitors do not know, in the one row every
    # one of them reads. The footer has it for whoever looks for the source.
    | def cur($key): $key != "" and $key == $current;
    # The bar's links are the menu's, in its order and words (README, "One
    # look everywhere"): Features, Help, then Support, gathered at the end
    # with the language and the blue Download button - one group, the logo
    # alone on the other side. Text only in the bar; the menu's rows carry
    # icons, a list read downwards.
    ([$links[] | select(.[2] == "features" or .[2] == "help") | . as [$href, $label, $key]
      | "      <a class=\"navlink\" href=\"\($href | h)\"\(if cur($key) then " aria-current=\"page\"" else "" end)>\($label | h)</a>\n"]
     | add) as $navlinks
    # The menu's rows: an icon in a tinted square and the words (no chevron:
    # it reads as a submenu, and each row goes straight to its page).
    # On a phone or tablet the download row sends the link to a computer
    # instead (menu-phone / menu-desk, nav.js): the one row an iPhone or iPad
    # app of dopeIPTV's own takes over once there is one.
    | def mrow($cls; $href; $label; $icon; $extra):
        "          <a role=\"menuitem\" class=\"navmenu-item\($cls)\" href=\"\($href | h)\"\($extra)><span class=\"mi-ico\" aria-hidden=\"true\"><svg viewBox=\"0 0 24 24\" focusable=\"false\">\($icon)</svg></span><span class=\"mi-t\">\($label | h)</span></a>\n";
    {features: "<rect x=\"3.5\" y=\"3.5\" width=\"7\" height=\"7\" rx=\"1.6\"/><rect x=\"13.5\" y=\"3.5\" width=\"7\" height=\"7\" rx=\"1.6\"/><rect x=\"3.5\" y=\"13.5\" width=\"7\" height=\"7\" rx=\"1.6\"/><rect x=\"13.5\" y=\"13.5\" width=\"7\" height=\"7\" rx=\"1.6\"/>",
     download: "<path d=\"M12 4v11m0 0-4.5-4.5M12 15l4.5-4.5M5 19.5h14\"/>",
     send: "<path d=\"M20.5 3.5 10 14M20.5 3.5l-6.6 17-3.9-6.5-6.5-3.9z\"/>",
     help: "<circle cx=\"12\" cy=\"12\" r=\"8.5\"/><path d=\"M9.6 9.4a2.5 2.5 0 0 1 4.8.9c0 1.7-2.4 2.2-2.4 3.7M12 17.2h.01\"/>",
     heart: "<path d=\"M12 21s-7.5-4.6-9.6-9.2C.9 8.4 3 4.5 6.9 4.5c2.1 0 3.6 1.1 5.1 3 1.5-1.9 3-3 5.1-3 3.9 0 6 3.9 4.5 7.3C19.5 16.4 12 21 12 21z\"/>"} as $ICON
    | def mcur($key): if cur($key) then " is-current" else "" end;
    def macur($key): if cur($key) then " aria-current=\"page\"" else "" end;
    (mrow(mcur("features"); $base + "#features"; t("nav_features"); $ICON.features; macur("features"))
     + mrow(" menu-desk"; $base + "#download"; t("nav_download"); $ICON.download; "")
     + mrow(" menu-phone"; "/#download"; t("menu_send_link"); $ICON.send;
            " data-send-link data-copied=\"\(t("dl_phone_copied") | h)\"")
     + mrow(mcur("help"); "/help/"; t("nav_help"); $ICON.help; macur("help"))) as $menuitems
    # Support: a quiet link beside the others, to the support page.
    | "      <a class=\"navlink navsupport\" href=\"/support/\"\(if $current == "support" then " aria-current=\"page\"" else "" end)><svg class=\"heart\" viewBox=\"0 0 24 24\" aria-hidden=\"true\" focusable=\"false\"><path d=\"M12 21s-7.5-4.6-9.6-9.2C.9 8.4 3 4.5 6.9 4.5c2.1 0 3.6 1.1 5.1 3 1.5-1.9 3-3 5.1-3 3.9 0 6 3.9 4.5 7.3C19.5 16.4 12 21 12 21z\"/></svg>\(t("support_nav") | h)</a>\n" as $navsupport
    | mrow(" navsupport" + mcur("support"); "/support/"; t("support_nav"); $ICON.heart; macur("support")) as $menusupport
    # Under the rows, small: feedback and the rules, without scrolling to
    # the footer.
    | ("          <div class=\"navmenu-foot\"><a href=\"#feedback\" data-feedback>\(t("foot_feedback") | h)</a><span aria-hidden=\"true\">·</span><a href=\"/privacy/\">\(t("foot_privacy") | h)</a><span aria-hidden=\"true\">·</span><a href=\"/transparency/\">\(t("foot_transparency") | h)</a><span aria-hidden=\"true\">·</span><a href=\"/terms/\">\(t("foot_terms") | h)</a></div>\n") as $menufoot
    # ...and on a phone, where the links fold into the menu, a heart of its
    # own in the header, one tap from anywhere.
    | "      <a class=\"navheart\" href=\"/support/\" aria-label=\"\(t("support_nav") | h)\" title=\"\(t("support_nav") | h)\"><svg viewBox=\"0 0 24 24\" aria-hidden=\"true\" focusable=\"false\"><path d=\"M12 21s-7.5-4.6-9.6-9.2C.9 8.4 3 4.5 6.9 4.5c2.1 0 3.6 1.1 5.1 3 1.5-1.9 3-3 5.1-3 3.9 0 6 3.9 4.5 7.3C19.5 16.4 12 21 12 21z\"/></svg></a>\n" as $navheart
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
                {navlinks: ($navlinks + (if $SUPPORT_ON then $navsupport + $navheart else "" end)),
                 langpick: $langpick,
                 menuitems: ($menuitems + (if $SUPPORT_ON then $menusupport else "" end) + $menufoot)}))
      + tip_i18n + feedback_i18n;
  # Every page's footer, one for all: dopeIPTV and what it is, the links in
  # three small groups, and under a hairline who makes it, where to reach
  # them and that it holds no channels - in plain sight on every page
  # (Stripe's review, the Swedish E-Commerce Act's section 8). A group is a
  # <details> open in the page: a computer, and a browser without nav.js,
  # show every link; on a phone nav.js folds them, a tap on a name opening
  # its group.
  def foot_group($head; $links):
    "      <details class=\"foot-col\" open>\n"
    + "        <summary><h2 class=\"foot-h\">\($head | h)</h2></summary>\n"
    + "        <ul>\n"
    + ([$links[] | "          <li><a href=\"\(.[0] | h)\"\(.[2] // "")>\(.[1] | h)</a></li>\n"] | add)
    + "        </ul>\n"
    + "      </details>\n";
  def site_footer:
    "<footer>\n"
    + "  <div class=\"wrap\">\n"
    + "    <div class=\"foot-top\">\n"
    + "      <div class=\"foot-brand\">\n"
    + "        <a class=\"foot-mark\" href=\"/\"><svg class=\"logo\" viewBox=\"0 0 100 100\" aria-hidden=\"true\" focusable=\"false\"><rect width=\"100\" height=\"100\" rx=\"22\"/><path d=\"M38 28v44l36-22z\"/></svg><b>dopeIPTV</b></a>\n"
    + "        <p class=\"foot-tag\">\(t("foot_tagline") | h)</p>\n"
    + (($R.version // "" | tostring) as $v
       | if ($v | truthy) then "        <p class=\"foot-ver\">v\($v | h)</p>\n" else "" end)
    + "      </div>\n"
    + "      <nav class=\"foot-cols\">\n"
    + foot_group(t("foot_h_app");
        [["/#features", t("nav_features")], ["/#download", t("nav_download")], ["/help/", t("nav_help")],
         ["https://github.com/slimture/dopeIPTV/releases", t("footer_releases")],
         ["https://github.com/slimture/dopeIPTV", "GitHub"]])
    + foot_group(t("foot_h_contact");
        [["#feedback", t("foot_feedback"), " data-feedback"], ["/about/", t("foot_about")]]
        + (if $SUPPORT_ON then [["/support/", t("support_nav")]] else [] end))
    + foot_group(t("foot_h_legal");
        [["/privacy/", t("foot_privacy")], ["/transparency/", t("foot_transparency")],
         ["/terms/", t("foot_terms")], ["/payments/", t("foot_payments")]])
    + "      </nav>\n"
    + "    </div>\n"
    + "    <div class=\"foot-base\">\n"
    + "      <p class=\"foot-company\">© \($year | h) <a href=\"https://solecraft.se/\" rel=\"noopener\">\($COMPANY | h)</a> · \(t("foot_orgnr") | h) <bdi>\($ORGNR | h)</bdi> · \(t("foot_place") | gsub("%TOWN%"; $TOWN) | h) · <a href=\"mailto:\($EMAIL | h)\">\($EMAIL | h)</a></p>\n"
    + "      <p class=\"foot-note\">\(t("foot_nocontent") | h)</p>\n"
    + "    </div>\n"
    + "  </div>\n"
    + "</footer>\n";
  def common:
    {lang: ($lang | h),
     dir: (if ($S.rtl | index($lang)) then "rtl" else "ltr" end),
     locale: ($S.locale[$lang] // "en_US" | h),
     css: ($asset_urls["/style.css"] | h), navjs: ($asset_urls["/nav.js"] | h),
     appjs: ($asset_urls["/app.js"] | h), helpjs: ($asset_urls["/help.js"] | h),
     featjs: ($asset_urls["/features.js"] // "/features.js" | h),
     # Without a hash (an older sync-releases.sh) still the file, not "null".
     eventsjs: ($asset_urls["/events.js"] // "/events.js" | h),
     year: $year, footer: site_footer};

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
  # The decimal sign: a comma where thousands are grouped with a point or a
  # space (formats.json), Persian its own.
  def dl_dec:
    ((($F.formats[$lang] // {}).group) // ",") as $g
    | if $lang == "fa" then "٫" elif $g == "," then "." else "," end;
  # A size in the reader's own way of writing a number: 157,3 MB in Swedish.
  def dl_size_l:
    dl_size as $s
    | ((($F.formats[$lang] // {}).digits) // "0123456789") as $digits
    | if $s == "" then "" else ($s | sub("\\."; dl_dec) | localdigits($digits)) end;
  def dl_href: ((.name // "" | tostring) as $name
      | (if $name != "" then "/dl.php?f=" + ($name | @uri) else (.url // "#") end));
  # Everything about a file that has no room on the page, for its tooltip.
  def dl_title:
    (._m as $m
     | dl_n(.name // "" | tostring) as $n
     | [$m.title, $m.arch, dl_size_l,
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
  # A card per system, side by side, all built alike so their rows line up:
  # its name, how it installs, the processor (Intel / AMD or ARM; a Mac's
  # Apple Silicon or Intel - a browser cannot tell), one button for that
  # file with its size, the format under it, and the system's other formats
  # in a list that folds out. app.js marks the visitor's own system; without
  # it the three are equal. Without :has() both files' buttons show.
  def dl_chip:
    if ._m.short == ".dmg" then ._m.cpu elif ._m.cpu == "ARM64" then "ARM" else "Intel / AMD" end;
  def dl_row:
    ._m as $m
    # The line under it without the file's ending, which its name says
    # (".zip — unzip & run"; a right-to-left language's mark before it,
    # Chinese's "——" without spaces).
    | ($m.fmt | sub("^\u200e?\\.[A-Za-z0-9]+[[:space:]]*—+[[:space:]]*"; "")) as $what
    | (if $m.cpu == "" then "" else dl_chip end) as $cpu
    | "<li><a href=\"\(dl_href | h)\" rel=\"nofollow\"\(dl_title)><b>\($m.title | h)</b><span>\([$what, ($cpu | h)] | map(select(. != "")) | join(" · "))</span><em>\(dl_size_l | h)</em></a></li>";
  def downloads($version; $reldate):
    ($R.assets // []) as $assets
    | ($assets | map(. + {_m: dl_meta(.name // "" | tostring)})) as $A
    | {"Linux": "linux", "macOS": "macos", "Windows": "windows"} as $ids
    | [("Linux", "macOS", "Windows") as $os
       | ($A | map(select(._m.os == $os)) | sort_by(._m.rank)) as $g
       | select($g | length > 0)
       | $g[0]._m.short as $main
       | ($g | map(select(._m.short == $main)) | .[:2]) as $pick
       | [$pick[] | .name] as $picknames
       | {os: $os, id: $ids[$os], g: $g, pick: $pick,
          rest: ($g | map(select(.name as $nm | $picknames | any(. == $nm) | not)))}] as $groups
    | (t("dl_go") | sub("[[:space:]]*[→←][[:space:]]*$"; "")) as $btn
    | if ($groups | length) == 0 then dl_github else
        "      <div class=\"dlx\" data-how-more=\"\(t("dl_how_more") | h)\" data-close=\"\(t("lb_close") | h)\">\n"
        + "        <div class=\"dlx-cards\">\n"
        + ([$groups[] | . as $grp
            | "          <article class=\"dlx-card\" data-os=\"\($grp.id)\">\n"
              + "            <span class=\"badge dlx-yours\">\(t("dl_yours") | h)</span>\n"
              + "            <h3>\($grp.os | h)</h3>\n"
              + "            <p class=\"dlx-sub\">\(t("dl_s_\($grp.id)") | h)</p>\n"
              + "            <div class=\"dlx-pick\" role=\"radiogroup\" aria-label=\"\(t("dl_pick") | h)\">"
              + ([$grp.pick | to_entries[] | .key as $k | .value
                  | "<input type=\"radio\" name=\"arch-\($grp.id)\" id=\"arch-\($grp.id)-\($k)\"\(if $k == 0 then " checked" else "" end)><label for=\"arch-\($grp.id)-\($k)\">\(dl_chip | h)</label>"] | add)
              + "</div>\n"
              + "            <div class=\"dlx-btns\">\n"
              + ([$grp.pick | to_entries[] | .key as $k | .value
                  | "              <a class=\"dlx-big\" data-k=\"\($k)\" href=\"\(dl_href | h)\" rel=\"nofollow\"\(dl_title)>\(dl_icon)<span>\($btn | h)</span><small>\(dl_size_l | h)</small></a>\n"] | add)
              + "            </div>\n"
              + "            <div class=\"dlx-meta\">"
              + ([$grp.pick | to_entries[] | .key as $k | .value._m as $m
                  | "<p data-k=\"\($k)\">\((if $m.short == ".dmg" then [$m.short, ($m.req // "")] else [$m.short, $m.arch] end) | map(select(. != "")) | map(h) | join(" · "))</p>"] | add)
              + "</div>\n"
              + "            <div class=\"dlx-foot\">\n"
              + (if ($grp.rest | length) > 0 then
                   "              <details class=\"dlx-more\"><summary>\(t("dl_other") | h) <span class=\"n\">\($grp.rest | length | fmt_count(.))</span></summary><ul>\([$grp.rest[] | dl_row] | join(""))</ul></details>\n"
                 elif $grp.pick[0]._m.short == ".dmg" then
                   "              <p class=\"dlx-hint\">\(t("dl_which_mac") | h)</p>\n"
                 else "" end)
              + "              <a class=\"dlx-how\" href=\"/help/#start-\($grp.id)\" data-how=\"\($grp.id)\">\(t("dl_how") | h)</a>\n"
              + "            </div>\n"
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
  # The band on the front page: the support page's top, small - the heart
  # icon, why in a sentence, and one button to the support page, where the
  # tip is. A soft glow behind it; the button's heart beats
  # while the band is in view (nav.js .is-live).
  def support_band:
      "  <section id=\"support\" class=\"pt0\">\n"
      + "    <div class=\"wrap\">\n"
      + "      <div class=\"supband supband-calm\">\n"
      + "        <img class=\"supband-icon\" src=\"/heart-icon.png?v=1\" width=\"64\" height=\"64\" alt=\"\" loading=\"lazy\">\n"
      + "        <h2>\(t("support_h2") | h)</h2>\n"
      + "        <p>\(t("support_p_short") | h)</p>\n"
      + "        <a class=\"btn primary supband-btn\" href=\"/support/\"><svg class=\"supband-heart\" viewBox=\"0 0 24 24\" aria-hidden=\"true\" focusable=\"false\"><path d=\"M12 21s-7.5-4.6-9.6-9.2C.9 8.4 3 4.5 6.9 4.5c2.1 0 3.6 1.1 5.1 3 1.5-1.9 3-3 5.1-3 3.9 0 6 3.9 4.5 7.3C19.5 16.4 12 21 12 21z\"/></svg>\(t("support_btn") | h)</a>\n"
      + "        <small>\(t("support_band_note") | h)</small>\n"
      + "      </div>\n"
      + "    </div>\n"
      + "  </section>\n\n";
  # The support page's tip, the thank-you nav.js fills in after a tip's
  # Payment Link, and the company. Without its address the tip says "coming soon". With the
  # dialog on it opens it (data-tip-dialog), and without nav.js goes to the
  # tip's Payment Link if there is one, else stays on this page.
  def support_way($cls; $title; $text; $href; $btn):
    "      <div class=\"sway \($cls)\">\n"
    + "        <h2>\($title | h)</h2>\n"
    + "        <p>\($text)</p>\n"
    + (if $TIPS_ON then
         "        <a class=\"btn primary\" href=\"\(if $href == "" then "/support/" else $href end | h)\" rel=\"nofollow noopener\" data-tip-dialog data-support=\"tip\">\($btn | h)</a>\n"
       elif $href == "" then
         "        <span class=\"btn primary is-off\" aria-disabled=\"true\">\(t("support_soon") | h)</span>\n"
       else
         "        <a class=\"btn primary\" href=\"\($href | h)\" rel=\"nofollow noopener\" data-support=\"tip\">\($btn | h)</a>\n"
       end)
    + "      </div>\n";
  # The support page's own parts (templates/support.html), in the reader's
  # language: the tip as one big card, what makes it safe, the thank-you
  # for an old receipt; then two short questions. The rules - the legal
  # text, in Swedish or English - are the payment terms (/payments/).
  def support_ways:
    def check: "<svg viewBox=\"0 0 24 24\" aria-hidden=\"true\" focusable=\"false\"><path d=\"M5 12.5l4.2 4.2L19 7\"/></svg>";
    "      <div class=\"support-ways is-one\">\n"
    + support_way("sway-tip"; t("support_tip_h"); t("support_tip_p") | h
                  | gsub("%AMOUNT%"; "<bdi class=\"sway-hint\">\($TIP_HINT | h)</bdi>");
                  $TIP_URL; t("support_tip_btn"))
    + "      </div>\n"
    + "      <ul class=\"sp-trust\">\n"
    + ([["support_trust_stripe", "support_trust_refund"][]
       | "        <li>\(check)\(t(.) | h)</li>\n"] | add)
    # The rules behind those, a page of the legal set (/payments/).
    + "        <li class=\"sp-trust-terms\"><svg viewBox=\"0 0 24 24\" aria-hidden=\"true\" focusable=\"false\"><path d=\"M7 3h7l5 5v13H7zM14 3v5h5M10 13h6M10 17h4\"/></svg><a href=\"/payments/\">\(t("foot_payments") | h)</a></li>\n"
    + "      </ul>\n"
    + "      <div id=\"support-thanks\" class=\"support-thanks\" hidden"
    + " data-thanks=\"\(t("support_thanks") | h)\""
    + "></div>\n";
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
  # still writes U+2028 and U+2029 escaped - and <, > and & escaped as
  # JSON_HEX_TAG | JSON_HEX_AMP do: it sits in a <script> element, which a
  # "</script>" in a release's tag or a translation would otherwise end.
  def php_json: tojson | gsub(" "; "\\u2028") | gsub(" "; "\\u2029")
    | gsub("<"; "\\u003c") | gsub(">"; "\\u003e") | gsub("&"; "\\u0026");
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
         support: (if $SUPPORT_ON then support_band else "" end),
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
  # "%SUPPORT%" in an answer is the link to the support page.
  def langlist:
    "<ul class=\"langlist\">"
    + ([$S.names | to_entries[] | select(.key as $k | $avail | index($k) != null)
        | "<li lang=\"\(.key | h)\"><bdi>\(.value | h)</bdi></li>"] | join(""))
    + "</ul>";
  # The help page (site/help.json): its topics in "groups" - each with its
  # icon, its title's key and the answers in it by id ("watch-rewind" is
  # help_watch_rewind_q / _a, so an answer keeps its id, and every link to
  # it, whichever topic it is filed under) -, the questions asked most in
  # "popular", the three steps to begin with in "steps", and an answer's
  # neighbours in "related". A topic of the features page links there.
  def hkey($id): "help_" + ($id | sub("-"; "_"));
  def hq($id): t(hkey($id) + "_q") as $q | if $q == hkey($id) + "_q" then null else $q end;
  def page_help:
    ($R.version // "" | tostring) as $version
    | "<svg class=\"ic\" viewBox=\"0 0 24 24\" aria-hidden=\"true\" focusable=\"false\">" as $svg
    | ($HELP.related // {}) as $REL
    | [$HELP.groups[] | . as $g | {k, title, features, icon,
        items: [.items[] | select(hq(.) != null)]} | select(.items | length > 0)] as $GROUPS
    | $tpl
    | expand(common + {canonical: (url("/help/"; $lang) | h)};
        {hreflang: hreflang("/help/"), nav: nav("help"),
         popular: ([($HELP.popular // [])[] | select(hq(.) != null)
           | "              <li><a href=\"#\(. | h)\">\(hq(.) | h)</a></li>\n"] | add // ""),
         steps: ([($HELP.steps // [])[] | . as $st
           | ($st.links // []) as $links
           | "            <li class=\"help-step\">"
             + (if $st.href then "<a class=\"help-step-a\" href=\"#\($st.href | h)\">" else "<div class=\"help-step-a\">" end)
             + "<h3>\(t("help_step_\($st.k)_h") | h)</h3><p>\(t("help_step_\($st.k)_p") | h)</p>"
             + (if ($links | length) > 0 then
                  "<span class=\"help-step-os\">" + ([$links[] | "<a href=\"#\(.[1] | h)\">\(.[0] | h)</a>"] | join("")) + "</span>"
                else "" end)
             + (if $st.href then "</a>" else "</div>" end)
             + "</li>\n"] | add // ""),
         toc: ([$GROUPS[]
           | "          <a href=\"#\(.k | h)\" data-toc=\"\(.k | h)\">\($svg)\(.icon)</svg><span class=\"l\">\(t(.title) | h)</span><span class=\"n\">\(.items | length)</span></a>\n"] | add // ""),
         sections: ([$GROUPS[] | . as $g
           | "          <section class=\"help-sec\" id=\"\($g.k | h)\" data-sec=\"\($g.k | h)\" aria-labelledby=\"\($g.k | h)-h\">\n"
             + "            <h2 id=\"\($g.k | h)-h\">\($svg)\($g.icon)</svg>\(t($g.title) | h)</h2>\n"
             + (t("help_g_\($g.k)_p") as $p | if $p != "help_g_\($g.k)_p" then "            <p class=\"help-sec-p\">\($p | h)</p>\n" else "" end)
             + ([$g.items[] as $id
                 | hq($id) as $q
                 | "            <details class=\"help-item\" id=\"\($id | h)\">\n"
                   + "              <summary><h3>\($q | h)</h3></summary>\n"
                   + "              <div class=\"help-a\">\(t(hkey($id) + "_a") | gsub("%LANGS%"; langlist) | gsub("%SUPPORT%"; "/support/"))"
                   # The settings page the answer is about, as the app shows
                   # it in the reader's language (tools/help_screenshots.py
                   # in the app's repository). Inside a closed <details>,
                   # so it loads when the question is opened.
                   + (if ($S.help_shots // [] | index($id)) then
                        "<img class=\"help-shot\" src=\"/help-shots/\($lang | h)/\($id | h).webp?v=\(pics_v($version) | h)\""
                        + " width=\"\($S.help_shot_size[0])\" height=\"\($S.help_shot_size[1])\""
                        + " loading=\"lazy\" decoding=\"async\" alt=\"\($q | h)\">"
                      else "" end)
                   + ([($REL[$id] // [])[] | select(hq(.) != null)
                       | "<a href=\"#\(. | h)\">\(hq(.) | h)</a>"] as $rel
                      | if ($rel | length) > 0 then
                          "<p class=\"help-rel\"><span>\(t("help_related") | h)</span>\($rel | join(""))</p>"
                        else "" end)
                   + "</div>\n"
                   + "            </details>\n"] | add // "")
             + (if ($g.features // "") != "" then
                  "            <a class=\"help-sec-more\" href=\"/features/#\($g.features | h)\">\(t("help_features_link") | h)</a>\n"
                else "" end)
             + "          </section>\n"] | add // "")});

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
             + "          </section>\n"] | add // "")});

# -- 404.php --------------------------------------------------------------------

  def page_404: $tpl | expand(common; {nav: nav("")});

# -- the legal pages: /privacy/, /terms/, /payments/, /about/ -----------------
# The text ($body, site/legal/<language>/<page>.html, which sync-releases.sh
# picks by the same rule) is Swedish or English; a page in any other language
# says so in the reader's language and points its canonical at the English.

  def legal_langs: $L.text | keys;
  def legal_hreflang($path):
    ([legal_langs[] as $c
      | "<link rel=\"alternate\" hreflang=\"\($c | h)\" href=\"\(url($path; $c) | h)\">\n"] | add)
    + "<link rel=\"alternate\" hreflang=\"x-default\" href=\"\($SITE + $path | h)\">\n";
  # A legal page's contents (its <nav class="legal-toc">) beside the text
  # rather than in it: on a computer it stays in view as you read and marks
  # the part you are in (nav.js); on a phone it is folded up over the text.
  def legal_toc($label):
    # Split, not index(): jq's index() counts bytes and slicing counts
    # characters, so a page with å, ä or ö was cut in the wrong place.
    "<nav class=\"legal-toc\"" as $open
    | (. / $open) as $p
    | if ($p | length) < 2 then {toc: "", body: ., grid: ""} else
        (($p[1:] | join($open)) / "</nav>") as $q
        | {toc: ("      <aside class=\"legal-side\">\n        <details class=\"legal-tocbox\" open>\n"
                 + "          <summary>\($label | h)</summary>\n          " + $open + $q[0] + "</nav>\n"
                 + "        </details>\n      </aside>\n"),
           body: ($p[0] + ($q[1:] | join("</nav>"))), grid: " has-toc"}
      end;
  # "3. Title" headings: the number in a mark of its own.
  def legal_numbers:
    gsub("<h2 id=\"(?<id>[^\"]+)\">(?<n>[0-9]+)\\. "; "<h2 id=\"\(.id)\"><span class=\"lnum\">\(.n)</span>");
  # The payment terms (legal.json's source: the support page's own rules,
  # one text in two forms) as a page of the legal set: each folded question
  # a numbered section, who you pay the last, the contents as on the others.
  def as_terms($label):
    (. / "<details class=\"sp-q\" id=\"") as $p
    | [$p[1:][] | capture("^(?<id>[^\"]+)\">\\s*<summary><h3>(?<t>[\\s\\S]*?)</h3></summary>\\s*<div class=\"sp-a\">\\s*(?<c>[\\s\\S]*?)</div>\\s*</details>\\s*(?<rest>[\\s\\S]*)$")] as $qs
    | (if ($qs | length) == 0 then null else
         [$qs[-1].rest | capture("<div class=\"sp-company\" id=\"(?<id>[^\"]+)\">\\s*<h3>(?<t>[\\s\\S]*?)</h3>\\s*(?<c>[\\s\\S]*?)</div>")] | first end) as $co
    | ([$qs[] | {id, t, c}] + (if $co != null then [$co] else [] end)) as $secs
    | ($p[0] | gsub("<p class=\"sp-intro\">"; "<p>"))
      + "<nav class=\"legal-toc\" aria-label=\"\($label | h)\">\n<ol>\n"
      + ([$secs[] | "<li><a href=\"#\(.id)\">\(.t)</a></li>\n"] | add)
      + "</ol>\n</nav>\n\n"
      + ([$secs | to_entries[] | "<h2 id=\"\(.value.id)\">\(.key + 1). \(.value.t)</h2>\n\(.value.c | rtrimstr("\n"))\n\n"] | add);
  # The support page's questions: four short ones in the visitor's own
  # language, the whole of it in the payment terms (/payments/), and who is
  # paid - so the page is the visitor's language all through.
  def support_faq:
    "<a href=\"mailto:\($EMAIL | h)\">\($EMAIL | h)</a>" as $mail
    | "      <div class=\"sp-legal\">\n"
      + ([[["what", "support_fq_what"], ["refund", "support_fq_refund"]][]
         | . as [$id, $k]
         | "        <details class=\"sp-q\" id=\"\($id)\">\n"
           + "          <summary><h3>\(t($k + "_q") | h)</h3></summary>\n"
           + "          <div class=\"sp-a\"><p>\(t($k + "_a") | h | gsub("%EMAIL%"; $mail))</p></div>\n"
           + "        </details>\n"] | add)
      + "        <p class=\"sp-more\"><a href=\"/payments/\">\(t("support_fq_more") | h)</a></p>\n"
      + "        <div class=\"sp-company\" id=\"company\">\n"
      + "          <h3>\(t("support_who_h") | h)</h3>\n"
      + "          <p>\($COMPANY | h) · \(t("foot_orgnr") | h) <bdi>\($ORGNR | h)</bdi> · \(t("foot_place") | gsub("%TOWN%"; $ADDRESS) | h)"
      + " · \(t("foot_vat") | h) <bdi>\($VAT | h)</bdi> · \($mail) · <a href=\"/about/#\(if $lang == "sv" then "foretaget" else "company" end)\">\(t("support_company_all") | h)</a></p>\n"
      + "        </div>\n"
      + "      </div>\n";
  def page_legal:
    (if ($L.text | has($lang)) then $lang else "en" end) as $cl
    | $L.page[$page][$cl] as $P
    | $L.text[$cl] as $X
    | "/\($page)/" as $path
    | (if $page == "support" then {toc: "", body: $body, grid: ""}
       else ($body | if $page == "payments" then as_terms($X.toc // "Contents") else . end
             | legal_numbers | legal_toc($X.toc // "Contents")) end) as $parts
    | $tpl
    # The support page in the visitor's own language; the legal set's
    # pages in the language they are written in.
    | expand(common + {title: (if $page == "support" then t("support_btn") else $P.title end | h),
                       desc: (if $page == "support" then t("support_p") else $P.desc end | h), clang: ($cl | h),
                       canonical: (url($path; $cl) | h), grid: $parts.grid};
        {hreflang: legal_hreflang($path), nav: nav($page),
         tabs: ([($L.tabs // $L.pages)[] as $p
           | "        <a href=\"/\($p | h)/\"\(if $p == $page then " aria-current=\"page\"" else "" end)>\(t("foot_\($p)") | h)</a>\n"]
           | add),
         note: (if $cl == $lang then "" else
           "      <p class=\"legal-note\">\(t("legal_only_en") | h)</p>\n" end),
         meta: ("<time datetime=\"\($L.updated | h)\">\($X.updated | h)</time> · \($X.governing | h)"
           + " <a href=\"\($path + "?lang=" + $X.link | h)\" hreflang=\"\($X.link | h)\" lang=\"\($X.link | h)\">\($X.other | h)</a>"),
         lede: (if ($P.lede // "") != "" then "        <p class=\"legal-lede\">\($P.lede | h)</p>\n" else "" end),
         toc: $parts.toc,
         body: (if $page == "support" then $body else $parts.body end),
         ways: (if $page == "support" then support_ways else "" end),
         faq: (if $page == "support" then support_faq else "" end)});

# -- /transparency/: Audit us -----------------------------------------------------
# What the privacy policy promises, shown in the code that does it. The words
# are the language's own (site/transparency/<lang>.html, the English where a
# language has none yet: $body, in $ARGS.named.blang); the code, the examples
# and the hosts are the same in all of them (site/transparency.json, made by
# tools/transparency.py from the app and the site at one version). In the
# words: <!--tp:code PART ID--> a block of code, <!--tp:ex PART KEY--> an
# example, <!--tp:tests PART--> the tests that check it, <!--tp:copy PART-->
# the button that copies the part's code with its question (.tp-ask),
# <!--tp:hosts KIND GROUP--> hosts the app's code names, %VERSION% and %REF%
# the version shown. A placeholder with nothing behind it stops the render.

  def tp_lines($a; $b): if $a == $b then "\($a)" else "\($a)–\($b)" end;
  def tp_code($TP; $sec; $id):
    ([$TP.code[$sec][]? | select(.id == $id)] | first) as $blk
    | if $blk == null then error("render.jq: no code \($sec) \($id) in transparency.json") else
      "<details class=\"tp-code\">\n"
      + "<summary><span class=\"tp-code-t\">\(t("tp_show_code") | h)</span> <span class=\"tp-code-f\" dir=\"ltr\">"
      + ([$blk.parts[] | "<a href=\"\(.url | h)\" rel=\"noopener\" title=\"\(t("tp_on_github") | h)\">\(.path | split("/") | last | h) \(tp_lines(.start; .end))</a>"] | join(" · "))
      + "</span></summary>\n"
      + ([$blk.parts[]
          | "<div class=\"tp-src\" data-for=\"\($sec | h)\" data-head=\"\(.path | h):\(tp_lines(.start; .end)) (dopeIPTV \($TP.version | h)) \(.url | h)\">\n"
            + "<a class=\"tp-src-h\" href=\"\(.url | h)\" rel=\"noopener\" dir=\"ltr\"><bdi>\(.path | h)</bdi> · \(t("tp_lines") | h) \(tp_lines(.start; .end))</a>\n"
            + "<pre><code>\(.code | h)</code></pre>\n</div>\n"] | add)
      + "</details>" end;
  def tp_ex($TP; $sec; $key):
    ($TP.examples[$sec][$key] // null) as $x
    | if $x == null then error("render.jq: no example \($sec) \($key) in transparency.json")
      else "<pre class=\"tp-ex\" dir=\"ltr\"><code>\($x | h)</code></pre>" end;
  def tp_tests($TP; $sec):
    if ($TP.tests[$sec] // []) == [] then error("render.jq: no tests \($sec) in transparency.json") else
      "<ul class=\"tp-tests\">\n"
      + ([$TP.tests[$sec][] | "<li><a href=\"\(.url | h)\" rel=\"noopener\"><code>\(.name | h)</code></a></li>\n"] | add)
      + "</ul>" end;
  def tp_hosts($TP; $kind; $group):
    [$TP.hosts[]? | select(.kind == $kind and .group == $group)] as $hs
    | if $hs == [] then error("render.jq: no \($kind) hosts \($group) in transparency.json") else
      [$hs[] | "<a class=\"tp-host\" href=\"\(.where[0].url | h)\" rel=\"noopener\" title=\"\(.where[0].path | h):\(.where[0].line)\"><code>\(.host | h)</code></a>"]
      | join(if $kind == "name" then ", " else "\n" end) end;
  def page_transparency:
    ($ARGS.named.tpfile[0] // error("render.jq: no transparency.json")) as $TP
    | ($ARGS.named.blang // "" | if . == "" then "en" else . end) as $bl
    | ($ARGS.named.tplangs // ["en"]) as $TPL
    | "/transparency/" as $path
    | ($body
       | gsub("%VERSION%"; $TP.version | h) | gsub("%REF%"; $TP.ref | h)
       | gsub("<!--tp:code (?<s>[a-z]+) (?<i>[a-z]+)-->"; tp_code($TP; .s; .i))
       | gsub("<!--tp:ex (?<s>[a-z]+) (?<k>[a-z]+)-->"; tp_ex($TP; .s; .k))
       | gsub("<!--tp:tests (?<s>[a-z]+)-->"; tp_tests($TP; .s))
       | gsub("<!--tp:hosts (?<k>[a-z]+) (?<g>[a-z]+)-->"; tp_hosts($TP; .k; .g))
       | gsub("<!--tp:copyall-->";
           "<p class=\"tp-copy-row\"><button type=\"button\" class=\"btn primary tp-copy\" data-for=\"all\" data-done=\"\(t("tp_copied") | h)\" hidden>\(t("tp_copy_all") | h)</button></p>")
       | gsub("<!--tp:copy (?<s>[a-z]+)-->";
           "<p class=\"tp-copy-row\"><button type=\"button\" class=\"btn primary tp-copy\" data-for=\"\(.s)\" data-done=\"\(t("tp_copied") | h)\" hidden>\(t("tp_copy") | h)</button></p>")
       | legal_numbers | legal_toc(t("tp_toc"))) as $parts
    | $tpl
    | expand(common + {title: (t("tp_title") | h), desc: (t("tp_desc") | h), blang: ($bl | h),
                       bdir: (if ($S.rtl | index($bl)) then "rtl" else "ltr" end),
                       canonical: (url($path; $bl) | h), grid: $parts.grid,
                       tpjs: ($asset_urls["/transparency.js"] // "/transparency.js" | h)};
        {hreflang: (([$TPL[] as $c
           | "<link rel=\"alternate\" hreflang=\"\($c | h)\" href=\"\(url($path; $c) | h)\">\n"] | add)
           + "<link rel=\"alternate\" hreflang=\"x-default\" href=\"\($SITE + $path | h)\">\n"),
         nav: nav("transparency"),
         tabs: ([($L.tabs // $L.pages)[] as $p
           | "        <a href=\"/\($p | h)/\"\(if $p == "transparency" then " aria-current=\"page\"" else "" end)>\(t("foot_\($p)") | h)</a>\n"]
           | add),
         note: (if $bl == $lang then "" else
           "      <p class=\"legal-note\">\(t("tp_only_en") | h)</p>\n" end),
         lede: "        <p class=\"legal-lede\">\(t("tp_lede") | h)</p>\n",
         meta: (t("tp_meta") | h | gsub("%VERSION%"; $TP.version | h) | gsub("%DATE%"; fmt_date($TP.made) | h)),
         toc: $parts.toc, body: $parts.body});

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
      # Audit us, in the languages it is written in.
      + (($ARGS.named.tpfile[0] // null) as $TP | ($ARGS.named.tplangs // []) as $TPL
         | if $TP == null then "" else
           ([$TPL[] as $code
             | "  <url>\n"
               + "    <loc>\(url("/transparency/"; $code) | h)</loc>\n"
               + "    <lastmod>\($TP.made | h)</lastmod>\n"
               + ([$TPL[] as $alt
                   | "    <xhtml:link rel=\"alternate\" hreflang=\"\($alt | h)\" href=\"\(url("/transparency/"; $alt) | h)\"/>\n"] | add)
               + "    <xhtml:link rel=\"alternate\" hreflang=\"x-default\" href=\"\($SITE + "/transparency/" | h)\"/>\n"
               + "  </url>\n"] | add // "") end)
      + "</urlset>\n";

  if $page == "index" then page_index
  elif $page == "help" then page_help
  elif $page == "features" then page_features
  elif $page == "404" then page_404
  elif $page == "sitemap" then page_sitemap
  elif $page == "transparency" then page_transparency
  elif ($L.pages | index($page)) != null then page_legal
  else error("render.jq: unknown page \($page)") end
