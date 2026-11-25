/* Everything dopeIPTV does (/features/): the groups filter the list, the box
 * searches it.
 *
 * An external file for the CSP (script-src 'self'), like help.js. Without it
 * the page is the whole list and the groups are links down it. With it a
 * group shows that group alone (its address, /features/#cast, opens it so),
 * every word typed has to appear in a feature for it to stay, and the
 * groups' counts follow the search. While every group shows, the group
 * being read is marked in the list as the page scrolls (on a phone its pill
 * is brought into the row's view). Motion is a short fade, and none where
 * the reader asks for less.
 */
(function () {
  var box = document.getElementById('flSearch');
  var index = document.getElementById('flIndex');
  if (!box || !index) return;

  var links = [].slice.call(index.querySelectorAll('a[data-g]'));
  var groups = [].slice.call(document.querySelectorAll('.fl-group'));
  var items = [].slice.call(document.querySelectorAll('.fl-item'));
  var count = document.getElementById('flCount');
  var none = document.getElementById('flNone');
  var clear = document.getElementById('flClear');
  var calm = window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches;
  var group = '';

  items.forEach(function (el) {
    // Only the words, not the help link's: "help" would match them all.
    var h = el.querySelector('h3'), p = el.querySelector('p');
    el.dataset.text = ((h ? h.textContent : '') + ' ' + (p ? p.textContent : '')).toLowerCase();
  });

  function show(el) {
    if (calm || !el.hidden) { el.hidden = false; return; }
    el.hidden = false;
    el.classList.remove('fl-in');
    void el.offsetWidth;           // the fade starts again for a group shown again
    el.classList.add('fl-in');
  }

  function apply() {
    var q = box.value.trim().toLowerCase();
    var terms = q ? q.split(/\s+/) : [];
    var per = {}, total = 0, shown = 0;
    items.forEach(function (el) {
      var t = el.dataset.text;
      var on = terms.every(function (w) { return t.indexOf(w) !== -1; });
      el.hidden = !on;
      if (on) {
        var g = el.closest('.fl-group').dataset.g;
        per[g] = (per[g] || 0) + 1;
        total++;
      }
    });
    groups.forEach(function (s) {
      var on = !!per[s.dataset.g] && (!group || group === s.dataset.g);
      if (on) { show(s); shown += per[s.dataset.g]; } else { s.hidden = true; }
    });
    links.forEach(function (a) {
      var g = a.dataset.g;
      var n = g ? (per[g] || 0) : total;
      var cur = g === group;
      a.querySelector('.n').textContent = n;
      a.classList.toggle('is-empty', n === 0 && !cur);
      if (cur) a.setAttribute('aria-current', 'true'); else a.removeAttribute('aria-current');
    });
    if (count) {
      count.hidden = !q;
      count.textContent = (count.dataset.fmt || '%n').replace('%n', shown);
    }
    if (none) none.hidden = shown > 0;
    if (clear) clear.hidden = !q;
    document.body.classList.toggle('searching', !!q);
    follow();
  }

  // The group being read, while every group shows: the last one whose
  // heading has passed a line near the window's middle - the last one at the
  // page's end, however short it is. At the page's top All stays marked;
  // a group picked, or a search, marks nothing more.
  var here = null, ticking = false;
  function follow() {
    var cur = '';
    if (!group && !box.value.trim() && window.scrollY > 40) {
      var line = window.innerHeight * 0.45;
      var atEnd = window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 2;
      groups.forEach(function (s) {
        if (!s.hidden && (atEnd || s.getBoundingClientRect().top <= line)) cur = s.dataset.g;
      });
    }
    if (cur === here) return;
    here = cur;
    index.classList.toggle('is-following', !!cur);
    links.forEach(function (a) {
      var on = !!cur && a.dataset.g === cur;
      a.classList.toggle('is-here', on);
      // On a phone the row scrolls sideways: the pill marked comes into
      // its view, the page itself not moved.
      if (on && index.scrollWidth > index.clientWidth) {
        var left = a.offsetLeft - (index.clientWidth - a.offsetWidth) / 2;
        index.scrollTo({ left: left, behavior: calm ? 'auto' : 'smooth' });
      }
    });
  }
  window.addEventListener('scroll', function () {
    if (ticking) return;
    ticking = true;
    window.requestAnimationFrame(function () { ticking = false; follow(); });
  }, { passive: true });
  window.addEventListener('resize', follow);

  function pick(g, scroll) {
    group = g;
    apply();
    if (history.replaceState) {
      history.replaceState(null, '', g ? '#' + g : location.pathname + location.search);
    }
    if (scroll) {
      var top = document.querySelector('.fl-main');
      var y = top.getBoundingClientRect().top + window.scrollY - 64;
      if (window.scrollY > y) window.scrollTo({ top: y, behavior: calm ? 'auto' : 'smooth' });
    }
  }

  links.forEach(function (a) {
    a.addEventListener('click', function (e) {
      e.preventDefault();
      // The group picked again shows everything again.
      pick(a.dataset.g === group ? '' : a.dataset.g, true);
      // On a phone the row scrolls: keep the one picked in view.
      if (a.scrollIntoView && index.scrollWidth > index.clientWidth) {
        a.scrollIntoView({ block: 'nearest', inline: 'center', behavior: calm ? 'auto' : 'smooth' });
      }
    });
  });
  // The row is a toolbar: the arrows move along it, Home and End to its ends.
  index.addEventListener('keydown', function (e) {
    var i = links.indexOf(document.activeElement);
    if (i < 0) return;
    var rtl = document.dir === 'rtl';
    var step = { ArrowDown: 1, ArrowUp: -1, ArrowRight: rtl ? -1 : 1, ArrowLeft: rtl ? 1 : -1 }[e.key];
    var to = e.key === 'Home' ? 0 : e.key === 'End' ? links.length - 1
      : step ? (i + step + links.length) % links.length : -1;
    if (to < 0) return;
    e.preventDefault();
    links[to].focus();
  });

  box.addEventListener('input', apply);
  box.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') { box.value = ''; apply(); }
  });
  if (clear) {
    clear.addEventListener('click', function () { box.value = ''; apply(); box.focus(); });
  }
  document.addEventListener('keydown', function (e) {
    if (e.key === '/' && document.activeElement !== box && !e.ctrlKey && !e.metaKey && !e.altKey) {
      e.preventDefault();
      box.focus();
      box.select();
    }
  });

  // An address with a group opens that group; one with a feature shows it.
  function arrive() {
    var id = decodeURIComponent(location.hash.slice(1));
    var g = links.some(function (a) { return a.dataset.g && a.dataset.g === id; }) ? id : '';
    group = g;
    apply();
    var el = id && !g && document.getElementById(id);
    if (el && el.classList.contains('fl-item')) el.scrollIntoView({ block: 'center' });
  }
  window.addEventListener('hashchange', arrive);
  arrive();
})();
