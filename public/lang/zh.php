<?php
/**
 * 中文（简体，zh）— iptv.dope.rs 的完整翻译。专有名词和技术术语
 * （Xtream Codes、M3U、AppImage、mpv、GitHub …）保留英文。字符串中的 HTML
 * 属性使用单引号，使数值保持为双引号 PHP 字符串。
 */
return [
    // <head>
    "meta_title"   => "dopeIPTV — 适用于 Linux 的 IPTV 播放器，支持 EPG 与时移",
    "meta_desc"    => "快速、开源的 IPTV 播放器，支持 Xtream Codes 与 M3U——完整 EPG 指南、时移、回看、录制和多画面。支持 Linux、macOS 与 Windows。",
    "meta_keywords"=> "IPTV 播放器, Linux IPTV 播放器, Xtream Codes, M3U, EPG, XMLTV, 时移, 回看电视, IPTV 录制, IPTV 多画面, 观看多个频道, 多屏 IPTV, IPTV Linux, IPTV macOS, IPTV Windows, dopeIPTV",

    // header nav
    "nav_features"    => "功能",
    "nav_screenshots" => "截图",
    "nav_download"    => "下载",
    "nav_github"      => "GitHub",
    "nav_download_btn"=> "下载",

    // hero
    "hero_eyebrow" => "正在直播 · 版本",
    "hero_h1"      => "直播、回看与录制——尽在一个<span class='hl'>原生桌面</span>应用中。",
    "hero_lede"    => "一款快速、现代的 IPTV 播放器，支持 Xtream Codes 和 M3U——具有完整的 EPG 节目指南、实时时移与回看、一键录制，以及同时最多九个频道的多画面。基于 mpv 构建，为 Linux 打造——也可在 macOS 和 Windows 上运行。",
    "hero_cta"     => "为你的系统下载",
    "hero_source"  => "查看源代码",
    "hero_free"    => "免费且开源",

    // feature strip chips
    "chip_timeshift" => "时移 / 回看",
    "chip_languages" => "26 种语言",

    // features
    "feat_eyebrow" => "一屏尽览",
    "feat_h2"      => "为人们真实的看电视方式而打造。",
    "feat_intro"   => "不是改装的媒体库——而是一个电视客户端，时间轴、节目指南和录制器都在你期望的位置。",
    "feat_c1_h" => "时移与回看",
    "feat_c1_p" => "在实时时间轴上回退到某个频道的存档，或从节目指南直接跳转到已播出的节目。",
    "feat_mv_h" => "多画面",
    "feat_mv_p" => "在网格中同时观看多达九个直播频道——混合不同的播放列表，点击其中一个切换音频，每个窗口都支持时移和字幕。",
    "feat_c2_h" => "暂停直播",
    "feat_c2_p" => "在直播之后进行 DVR 式的暂停和继续——播放器会准确显示你落后了多少。",
    "feat_c3_h" => "完整的 EPG 指南",
    "feat_c3_p" => "真正的节目网格，带搜索、提醒和可配置的“接下来播出”列表。",
    "feat_c4_h" => "一键录制",
    "feat_c4_p" => "通过单个连接录制你正在观看的流，带定时器、大小限制和录制库。",
    "feat_c5_h" => "多提供商",
    "feat_c5_p" => "多个 Xtream 或 M3U 播放列表并排排列，各自拥有独立的 EPG、自动刷新和自定义指南 URL。",
    "feat_c6_h" => "流畅播放",
    "feat_c6_p" => "内置 mpv 引擎，支持 Chromecast、Trakt 同步、主题和完整的键盘控制。",

    // screenshots
    "shots_eyebrow" => "内部一览",
    "shots_h2"      => "简洁、深色，绝不碍事。",
    "shot_ph"       => "截图",
    "shot_main_alt" => "dopeIPTV 主窗口，包含频道列表、节目指南和视频",
    "shot_main_t"   => "频道与播放器",
    "shot_main_c"   => "列表、指南和视频集于一个布局。",
    "shot_epg_alt"  => "dopeIPTV EPG 节目指南网格",
    "shot_epg_t"    => "节目指南",
    "shot_epg_c"    => "带回看标记的网格视图。",
    "shot_ts_alt"   => "dopeIPTV 时移时间轴在浏览某个频道的存档",
    "shot_ts_t"     => "时移时间轴",
    "shot_ts_c"     => "浏览存档，直播边缘已标记。",
    "shot_rec_alt"  => "带定时器的 dopeIPTV 录制库",
    "shot_rec_t"    => "录制",
    "shot_rec_c"    => "定时器、存储限制和播放。",

    // download
    "dl_eyebrow" => "获取 dopeIPTV",
    "dl_h2"      => "下载最新版本。",
    "dl_latest"  => "最新",
    "os_help_linux"   => "不确定？选 <b>AppImage</b>——它无需安装即可在任何发行版上运行。在 Debian/Ubuntu 上选 <b>.deb</b>。除非你用的是 ARM 机器（Raspberry Pi、ARM 服务器），否则选 <b>Intel / AMD</b>。",
    "os_help_macos"   => "一个镜像同时适用于 Apple Silicon（M 系列）和 Intel Mac。",
    "os_help_windows" => "便携版——解压即用，无需安装。最新的平台，仍在打磨中。",
    "os_install_linux"   => "🐧 <b>AppImage：</b>赋予可执行权限并运行——无需安装：<code>chmod +x dopeIPTV-*.AppImage &amp;&amp; ./dopeIPTV-*.AppImage</code>。<b>.deb</b>（Debian/Ubuntu）：<code>sudo apt install ./dopeIPTV-*.deb</code>。<b>.rpm</b>（Fedora/RHEL）：<code>sudo dnf install ./dopeIPTV-*.rpm</code>。",
    "os_install_macos"   => "🍎 打开 <code>.dmg</code> 并将 dopeIPTV 拖到“应用程序”。由于该应用尚未经过 Apple 公证（notarized），首次启动可能被阻止——<b>右键点击应用 → 打开</b>，然后在对话框中点<b>打开</b>（或在<b>系统设置 → 隐私与安全性 → 仍要打开</b>中允许）。如果 macOS 反而提示应用<b>“已损坏”</b>，请在“终端”中移除下载标记：<code>xattr -dr com.apple.quarantine /Applications/dopeIPTV.app</code>。这是安全的——该警告只是表示此构建未签名。",
    "os_install_windows" => "🪟 解压文件夹并运行 <code>dopeiptv.exe</code>。由于该应用尚未签名，SmartScreen 可能会显示<b>“Windows 已保护你的电脑”</b>——点击<b>更多信息 → 仍要运行</b>。这只是一个警告，不会阻止或删除任何内容。",
    "arch_apple"     => "Apple Silicon 与 Intel",
    "arch_x86"       => "Intel / AMD（64 位）",
    "arch_arm"       => "ARM（64 位）",
    "arch_universal" => "通用",
    "dl_t_dmg"      => "macOS 磁盘映像",
    "dl_f_dmg"      => ".dmg——拖到“应用程序”",
    "dl_t_pkg"      => "macOS 安装程序",
    "dl_f_pkg"      => ".pkg",
    "dl_t_exe"      => "Windows 安装程序",
    "dl_f_exe"      => ".exe",
    "dl_t_winzip"   => "Windows 便携版",
    "dl_f_winzip"   => ".zip——解压即用，无需安装",
    "dl_t_appimage" => "AppImage",
    "dl_f_appimage" => "可在任何发行版上运行——无需安装",
    "dl_t_deb"      => ".deb 软件包",
    "dl_f_deb"      => "适用于 Debian / Ubuntu",
    "dl_t_rpm"      => ".rpm 软件包",
    "dl_f_rpm"      => "适用于 Fedora / RHEL",
    "dl_t_flatpak"  => "Flatpak",
    "dl_f_flatpak"  => "所有发行版",
    "dl_recommended"=> "推荐",
    "dl_go"         => "下载 →",
    "dl_all_name"   => "GitHub 上的所有软件包",
    "dl_all_sub"    => "最新发布",
    "dl_open"       => "打开 →",
    "note_generated" => "↻ 在服务器上根据 <code>slimture/dopeIPTV</code> 的 GitHub 发布自动生成——新构建会自动出现。",
    "note_verify"    => "🔒 验证你的下载——<a class='verify-link' href='/files/SHA256SUMS'>SHA-256 校验和</a> · <code>sha256sum -c SHA256SUMS</code>",

    // credits
    "cred_eyebrow" => "开源",
    "cred_h2"      => "自由软件，站在巨人的肩膀上。",
    "cred_intro"   => "dopeIPTV 是基于 GPL-3.0 许可证的<b>自由开源软件</b>——没有广告，没有跟踪，没有账户。它构建于以下项目和服务之上，并对它们心怀感激：",
    "cred_playback"      => "播放",
    "cred_interface"     => "界面",
    "cred_casting"       => "投屏",
    "cred_metadata"      => "元数据与图片",
    "cred_watched"       => "观看同步",
    "cred_licences"      => "许可证",
    "cred_licences_link" => "GPL-3.0 与第三方",
    "disclaimer" => "本产品使用 TMDB API，但未获得 TMDB 的认可或认证。本产品使用 Trakt API，但未获得 Trakt 的认可或认证。所有商标均为其各自所有者的财产。",

    // footer
    "footer_releases" => "发布",
    "footer_docs"     => "文档",
    "lang_label"      => "语言",
];
