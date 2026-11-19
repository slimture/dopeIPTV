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
# Support (the support page, /support/), on dopeIPTV's own Stripe account:
# a tip is given in the tip dialog on the page (nav.js: Stripe's card form,
# Apple Pay and Google Pay, donate/donate.py) once SUPPORT_TIPS=on, else
# through a Stripe Payment Link (SUPPORT_TIP_URL); monthly support is a
# Payment Link (SUPPORT_MONTHLY_URL); and GitHub Sponsors. They are the
# server's, not git's: sync-releases.sh reads them from
# /usr/local/etc/iptv-site.conf and passes them here. A way with none of
# them says "coming soon" and links nowhere. What the page suggests - a
# tip's amount, the monthly one - is set beside them (SUPPORT_TIP_HINT,
# SUPPORT_MONTHLY_PRICE). SUPPORT_MONTHLY_PRICES - "SEK 29, EUR 2.50, USD 3",
# the monthly price's own currency first - are the amounts its Stripe price
# has in each currency; nav.js shows the visitor's (Stripe charges the one of
# where they are), SUPPORT_MONTHLY_PRICE (else the first) without nav.js.
| (($ARGS.named.tips // "") == "on") as $TIPS_ON
| ($ARGS.named.tip_url // "") as $TIP_URL
| ($ARGS.named.monthly_url // "") as $MONTHLY_URL
| ($ARGS.named.sponsors_url // "") as $SPONSORS_URL
# Stripe's customer portal: where a monthly supporter changes their card or
# cancels (SUPPORT_PORTAL_URL, its login link: they get a link by e-mail).
| ($ARGS.named.portal_url // "") as $PORTAL_URL
| (($ARGS.named.tip_hint // "") | if . == "" then "50 kr" else . end) as $TIP_HINT
| [($ARGS.named.monthly_prices // "") | splits("[,;](?=\\s*[A-Za-z])")
   | gsub("^\\s+|\\s+$"; "") | capture("^(?<c>[A-Za-z]{3})\\s+(?<a>[0-9]+([.,][0-9]{1,2})?)$")?
   | {key: (.c | ascii_downcase), value: (.a | sub(","; "."))}] as $PRICE_LIST
| ($PRICE_LIST | from_entries) as $MONTHLY_PRICES
| (($ARGS.named.monthly_price // "")
   | if . != "" then . elif ($PRICE_LIST | length) > 0
     then "\($PRICE_LIST[0].value) \($PRICE_LIST[0].key | ascii_upcase)" else "30 kr" end) as $MONTHLY_PRICE
# The ways to pay are invited to - the header's link and heart, the front
# page's band, the footer's link - only once one is set up: until then the
# support page is there (its policy, GitHub Sponsors, "coming soon"), but
# nothing on the site asks for money that cannot be taken. Every one of them
# goes to the support page, where both ways are - as the app's heart does;
# its tip button opens the tip dialog there (data-tip-dialog).
| ($TIPS_ON or $TIP_URL != "" or $MONTHLY_URL != "") as $SUPPORT_ON
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
  # The tip dialog's words (nav.js), on every page while it is on. The
  # amounts, the currencies and Stripe's publishable key come from the
  # service (/donate/config).
  def tip_i18n:
    if $TIPS_ON | not then "" else
      "<div id=\"tip-i18n\" hidden"
      + ([[["title", "support_btn"], ["sub", "tip_sub"], ["other", "tip_other"], ["othershort", "tip_other_short"],
           ["currency", "tip_currency"], ["cont", "tip_continue"], ["email", "tip_email"], ["pay", "tip_pay"],
           ["busy", "tip_busy"], ["lock", "support_stripe"], ["back", "tip_back"],
           ["thanks", "support_thanks"], ["receipt", "tip_receipt"], ["failed", "tip_failed"],
           ["emailmissing", "tip_email_missing"], ["range", "tip_range"],
           ["close", "donate_close"], ["loading", "support_loading"],
           ["apple", "tip_apple"], ["google", "tip_google"], ["card", "tip_card"],
           ["key", "support_key"], ["keyhow", "support_key_how"], ["keycopy", "support_key_copy"],
           ["keycopied", "support_key_copied"], ["keyopen", "support_key_open"], ["keywait", "tip_key_wait"],
           ["keynone", "support_key_none"], ["payeenote", "tip_payee_note"], ["more", "tip_more"]][]
          | . as [$k, $key] | " data-\($k)=\"\(t($key) | h)\""] | add)
      + " data-payee=\"\($COMPANY | h)\" data-payeelogo=\"\($PAYEE_LOGO | h)\" data-payeeurl=\"\($PAYEE_URL | h)\""
      + " data-moremail=\"\($EMAIL | h)\""
      + (if $MONTHLY_URL == "" then "" else " data-monthly=\"\(t("support_monthly_h") | h)\"" end)
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
    # The menu's rows: an icon in a tinted square, the words, a chevron (CSS).
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
    | "      <a class=\"navlink navdonate\" href=\"/support/\"\(if $current == "support" then " aria-current=\"page\"" else "" end)><svg class=\"heart\" viewBox=\"0 0 24 24\" aria-hidden=\"true\" focusable=\"false\"><path d=\"M12 21s-7.5-4.6-9.6-9.2C.9 8.4 3 4.5 6.9 4.5c2.1 0 3.6 1.1 5.1 3 1.5-1.9 3-3 5.1-3 3.9 0 6 3.9 4.5 7.3C19.5 16.4 12 21 12 21z\"/></svg>\(t("support_nav") | h)</a>\n" as $navdonate
    | mrow(" navdonate" + mcur("support"); "/support/"; t("support_nav"); $ICON.heart; macur("support")) as $menudonate
    # Under the rows, small: feedback and the rules, without scrolling to
    # the footer.
    | ("          <div class=\"navmenu-foot\"><a href=\"#feedback\" data-feedback>\(t("foot_feedback") | h)</a><span aria-hidden=\"true\">·</span><a href=\"/privacy/\">\(t("foot_privacy_terms") | h)</a></div>\n") as $menufoot
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
                {navlinks: ($navlinks + (if $SUPPORT_ON then $navdonate + $navheart else "" end)),
                 langpick: $langpick,
                 menuitems: ($menuitems + (if $SUPPORT_ON then $menudonate else "" end) + $menufoot)}))
      + tip_i18n + feedback_i18n;
  # Every page's footer, one for all: dopeIPTV and what it is, the links in
  # three small groups, and under a hairline who makes it, where to reach
  # them and that it holds no channels - in plain sight on every page
  # (Stripe's review, the Swedish E-Commerce Act's section 8).
  def foot_group($head; $links):
    "      <div class=\"foot-col\">\n"
    + "        <h2 class=\"foot-h\">\($head | h)</h2>\n"
    + "        <ul>\n"
    + ([$links[] | "          <li><a href=\"\(.[0] | h)\"\(.[2] // "")>\(.[1] | h)</a></li>\n"] | add)
    + "        </ul>\n"
    + "      </div>\n";
  def site_footer:
    "<footer>\n"
    + "  <div class=\"wrap\">\n"
    + "    <div class=\"foot-top\">\n"
    + "      <div class=\"foot-brand\">\n"
    + "        <a class=\"foot-mark\" href=\"/\"><svg class=\"logo\" viewBox=\"0 0 100 100\" aria-hidden=\"true\" focusable=\"false\"><rect width=\"100\" height=\"100\" rx=\"22\"/><path d=\"M38 28v44l36-22z\"/></svg><b>dopeIPTV</b></a>\n"
    + "        <p class=\"foot-tag\">\(t("foot_tagline") | h)</p>\n"
    + "      </div>\n"
    + "      <nav class=\"foot-cols\">\n"
    + foot_group(t("foot_h_app");
        [["/#download", t("nav_download")], ["/help/", t("nav_help")],
         ["https://github.com/slimture/dopeIPTV/releases", t("footer_releases")],
         ["https://github.com/slimture/dopeIPTV", "GitHub"]])
    + foot_group(t("foot_h_contact");
        [["#feedback", t("foot_feedback"), " data-feedback"], ["/about/", t("foot_about")]]
        + (if $SUPPORT_ON then [["/support/", t("support_nav")]] else [] end))
    + foot_group(t("foot_h_legal");
        [["/privacy/", t("foot_privacy_terms")], ["/payments/", t("foot_payments")]])
    + "      </nav>\n"
    + "    </div>\n"
    + "    <div class=\"foot-base\">\n"
    + "      <div class=\"foot-maker\">\n"
    + "        <a class=\"sole\" href=\"https://solecraft.se/\" rel=\"noopener\" aria-label=\"\(t("footer_built_by") | h)\" data-tip=\"\(t("footer_built_by") | h)\"><span class=\"sole-logo\">Solecraft</span></a>\n"
    + "        <span class=\"v\">dopeIPTV\(($R.version // "" | tostring) as $v | if ($v | truthy) then " " + ($v | h) else "" end) · © \($year | h) Solecraft AB</span>\n"
    + "      </div>\n"
    + "      <p class=\"foot-company\">\(t("foot_made_by") | gsub("%COMPANY%"; $COMPANY) | h) · \(t("foot_orgnr") | h) <bdi>\($ORGNR | h)</bdi> · \(t("foot_place") | gsub("%TOWN%"; $TOWN) | h) · <a href=\"mailto:\($EMAIL | h)\">\($EMAIL | h)</a> · \(t("foot_nocontent") | h)</p>\n"
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
  # The band on the front page: the support page's top, small - the
  # supporter's icon, why in a sentence, and one button to the support page,
  # where both ways are. A soft glow behind it; the button's heart beats
  # while the band is in view (nav.js .is-live).
  def support_band:
      "  <section id=\"support\" class=\"pt0\">\n"
      + "    <div class=\"wrap\">\n"
      + "      <div class=\"donate donate-calm\">\n"
      + "        <img class=\"donate-icon\" src=\"/supporter-icon.png?v=1\" width=\"64\" height=\"64\" alt=\"\" loading=\"lazy\">\n"
      + "        <h2>\(t("support_h2") | h)</h2>\n"
      + "        <p>\(t("support_p_short") | h)</p>\n"
      + "        <a class=\"btn primary donate-btn\" href=\"/support/\"><svg class=\"donate-heart\" viewBox=\"0 0 24 24\" aria-hidden=\"true\" focusable=\"false\"><path d=\"M12 21s-7.5-4.6-9.6-9.2C.9 8.4 3 4.5 6.9 4.5c2.1 0 3.6 1.1 5.1 3 1.5-1.9 3-3 5.1-3 3.9 0 6 3.9 4.5 7.3C19.5 16.4 12 21 12 21z\"/></svg>\(t("support_btn") | h)</a>\n"
      + "        <small>\(t("support_band_note") | h)</small>\n"
      + "      </div>\n"
      + "    </div>\n"
      + "  </section>\n\n";
  # The support page's ways to give (in the support page's legal text where
  # it says <!--SUPPORT-OPTIONS-->), the thank-you nav.js fills in after
  # paying, and the company. A way without its address says "coming soon".
  # A tip with the dialog on opens it (data-tip-dialog), and without nav.js goes
  # to the tip's Payment Link if there is one, else stays on this page.
  def support_way($cls; $title; $text; $href; $btn; $primary; $ev):
    ($ev == "tip" and $TIPS_ON) as $dialog
    | "      <div class=\"sway \($cls)\"\(if $ev == "monthly" then " id=\"monthly\"" else "" end)>\n"
    + "        <h2>\($title | h)</h2>\n"
    + "        <p>\($text)</p>\n"
    + (if $dialog then
         "        <a class=\"btn\(if $primary then " primary" else "" end)\" href=\"\(if $href == "" then "/support/" else $href end | h)\" rel=\"nofollow noopener\" data-tip-dialog data-support=\"\($ev)\">\($btn | h)</a>\n"
       elif $href == "" then
         "        <span class=\"btn\(if $primary then " primary" else "" end) is-off\" aria-disabled=\"true\">\(t("support_soon") | h)</span>\n"
       else
         "        <a class=\"btn\(if $primary then " primary" else "" end)\" href=\"\($href | h)\" rel=\"nofollow noopener\" data-support=\"\($ev)\">\($btn | h)</a>\n"
       end)
    + (if $ev == "monthly" and $PORTAL_URL != "" then
         "        <a class=\"sway-manage\" href=\"\($PORTAL_URL | h)\" rel=\"nofollow noopener\">\(t("support_manage") | h)</a>\n"
       else "" end)
    + "      </div>\n";
  # The support page's own parts (templates/support.html), in the reader's
  # language: the two ways as big cards - a tip, monthly support with the
  # customer portal under it -, GitHub Sponsors as a line once it is set up,
  # what makes it safe, the thank-you nav.js fills in after paying; what a
  # supporter gets and where the money goes. The rules - the legal text, in
  # Swedish or English - fold up below them (site/legal/<lang>/support.html).
  def support_ways:
    def check: "<svg viewBox=\"0 0 24 24\" aria-hidden=\"true\" focusable=\"false\"><path d=\"M5 12.5l4.2 4.2L19 7\"/></svg>";
    "      <div class=\"support-ways\">\n"
    + support_way("sway-tip"; t("support_tip_h"); t("support_tip_p") | h
                  | gsub("%AMOUNT%"; "<bdi class=\"sway-hint\">\($TIP_HINT | h)</bdi>");
                  $TIP_URL; t("support_tip_btn"); true; "tip")
    + support_way("sway-monthly"; t("support_monthly_h"); t("support_monthly_p") | h
                  | gsub("%AMOUNT%"; "<bdi class=\"sway-price\"\(if ($PRICE_LIST | length) > 0
                      then " data-prices=\"\($MONTHLY_PRICES | tojson | h)\" data-base=\"\($PRICE_LIST[0].key | h)\"" else "" end)>\($MONTHLY_PRICE | h)</bdi>");
                  $MONTHLY_URL; t("support_monthly_btn"); true; "monthly")
    + "      </div>\n"
    + (if $SPONSORS_URL != "" then
         "      <p class=\"sp-gh\"><a href=\"\($SPONSORS_URL | h)\" rel=\"nofollow noopener\" data-support=\"github\">\(t("support_gh_btn") | h)</a> · \(t("support_gh_p") | h)</p>\n"
       else "" end)
    + "      <ul class=\"sp-trust\">\n"
    + ([["support_trust_stripe", "support_trust_refund", "support_trust_cancel"][]
       | "        <li>\(check)\(t(.) | h)</li>\n"] | add)
    # The rules behind those three, a page of the legal set (/payments/).
    + "        <li class=\"sp-trust-terms\"><svg viewBox=\"0 0 24 24\" aria-hidden=\"true\" focusable=\"false\"><path d=\"M7 3h7l5 5v13H7zM14 3v5h5M10 13h6M10 17h4\"/></svg><a href=\"/payments/\">\(t("foot_payments") | h)</a></li>\n"
    + "      </ul>\n"
    + "      <div id=\"support-thanks\" class=\"support-thanks\" hidden"
    + ([[["thanks", "support_thanks"], ["again", "support_again"], ["key", "support_key"],
         ["keyhow", "support_key_how"], ["keycopy", "support_key_copy"], ["keycopied", "support_key_copied"],
         ["keyopen", "support_key_open"], ["keywait", "support_key_wait"], ["keynone", "support_key_none"],
         ["loading", "support_loading"]][]
        | . as [$k, $key] | " data-\($k)=\"\(t($key) | h)\""] | add)
    + "></div>\n";
  def support_perks:
    def theme($cls; $name):
      "<span class=\"tp \($cls)\" aria-hidden=\"true\"><i class=\"tp-side\"></i><i class=\"tp-main\"><b></b><b></b><b class=\"on\"></b><b></b></i></span>";
    "      <div class=\"sp-perks-grid\">\n"
    + "        <div class=\"perk perk-themes\"><div class=\"perk-art\">\(theme("tp-aurora"; "Aurora"))\(theme("tp-dawn"; "Dawn"))</div><h3>\(t("support_perk_themes") | h)</h3><p>Aurora · Dawn</p></div>\n"
    + "        <div class=\"perk perk-icon\"><div class=\"perk-art\"><img src=\"/supporter-icon.png?v=1\" width=\"64\" height=\"64\" alt=\"\"></div><h3>\(t("support_perk_icon") | h)</h3></div>\n"
    + "        <div class=\"perk perk-heart\"><div class=\"perk-art\"><svg viewBox=\"0 0 24 24\" aria-hidden=\"true\" focusable=\"false\"><path d=\"M12 21s-7.5-4.6-9.6-9.2C.9 8.4 3 4.5 6.9 4.5c2.1 0 3.6 1.1 5.1 3 1.5-1.9 3-3 5.1-3 3.9 0 6 3.9 4.5 7.3C19.5 16.4 12 21 12 21z\"/></svg></div><h3>\(t("support_perk_heart") | h)</h3></div>\n"
    + "        <div class=\"perk perk-feedback\"><div class=\"perk-art\"><svg viewBox=\"0 0 24 24\" aria-hidden=\"true\" focusable=\"false\"><path d=\"M4 5.5A2.5 2.5 0 0 1 6.5 3h11A2.5 2.5 0 0 1 20 5.5v8a2.5 2.5 0 0 1-2.5 2.5H10l-4.5 4v-4h0A1.5 1.5 0 0 1 4 14.5z\"/></svg></div><h3>\(t("support_perk_feedback") | h)</h3></div>\n"
    + "      </div>\n";
  def support_money:
    "      <ul class=\"sp-money-list\">\n"
    + ([[["support_money_dev", "<path d=\"M8 7l-5 5 5 5M16 7l5 5-5 5M14 4l-4 16\"/>"],
        ["support_money_servers", "<rect x=\"4\" y=\"4\" width=\"16\" height=\"6.5\" rx=\"2\"/><rect x=\"4\" y=\"13.5\" width=\"16\" height=\"6.5\" rx=\"2\"/><path d=\"M8 7.2h.01M8 16.7h.01\"/>"],
        ["support_money_apple", "<rect x=\"3\" y=\"4\" width=\"18\" height=\"12\" rx=\"2\"/><path d=\"M8 20h8M12 16v4\"/>"]][]
       | "        <li><svg viewBox=\"0 0 24 24\" aria-hidden=\"true\" focusable=\"false\">\(.[1])</svg><span>\(t(.[0]) | h)</span></li>\n"] | add)
    + "      </ul>\n";
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
         donate: (if $SUPPORT_ON then support_band else "" end),
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
                   + "              <div class=\"help-a\">\(t(hkey($id) + "_a") | gsub("%LANGS%"; langlist) | gsub("%DONATE%"; "/support/"))"
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

# -- the legal pages: /privacy/, /terms/, /donations/, /about/ ----------------
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
    | "<a href=\"\((if $PORTAL_URL != "" then $PORTAL_URL else "#monthly" end) | h)\">\(t("support_manage") | h)</a>" as $manage
    | "      <div class=\"sp-legal\">\n"
      + ([[["cancel", "support_fq_cancel"], ["refund", "support_fq_refund"], ["key", "support_fq_key"], ["again", "support_fq_lost"]][]
         | . as [$id, $k]
         | "        <details class=\"sp-q\" id=\"\($id)\">\n"
           + "          <summary><h3>\(t($k + "_q") | h)</h3></summary>\n"
           + "          <div class=\"sp-a\"><p>\(t($k + "_a") | h | gsub("%EMAIL%"; $mail) | gsub("%MANAGE%"; $manage))</p></div>\n"
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
    | expand(common + {title: ($P.title | h), desc: ($P.desc | h), clang: ($cl | h),
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
         body: ((if $page == "support" then $body else $parts.body end)
                | gsub("%PORTAL%"; (if $PORTAL_URL != "" then $PORTAL_URL else "/support/#monthly" end) | h)),
         ways: (if $page == "support" then support_ways else "" end),
         faq: (if $page == "support" then support_faq else "" end),
         perks: (if $page == "support" then support_perks else "" end),
         money: (if $page == "support" then support_money else "" end)});

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
