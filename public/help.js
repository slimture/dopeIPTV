/* Help page: live search, the folded questions opened where needed, and the
 * topics beside them marking where you are.
 *
 * An EXTERNAL file, not an inline <script>. The site's CSP is
 * `script-src 'self'` with no 'unsafe-inline' (the site's CSP), so an
 * inline block is silently blocked - which is exactly why the first version
 * of this search did nothing at all on the live site.
 *
 * Every answer is in the page as a <details>, so filtering is showing and
 * hiding, and what matches is opened: a hit reads without a second click.
 * A link to a question (/help/#start-linux, from the download cards, the
 * features page, a related answer) opens that one. With this file absent
 * the page is still complete - the questions open by hand - just unfiltered.
 */
(function () {
  var box = document.getElementById('helpSearch');
  if (!box) return;

  var items = [].slice.call(document.querySelectorAll('.help-item'));
  var secs = [].slice.call(document.querySelectorAll('.help-sec'));
  var tocs = [].slice.call(document.querySelectorAll('[data-toc]'));
  var count = document.getElementById('helpCount');
  var none = document.getElementById('helpNone');
  var clear = document.getElementById('helpClear');
  var toc = document.getElementById('helpToc');
  var calm = window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches;

  // What a question is searched by: itself and its answer - not the related
  // answers' names at its foot, which would make it match their words too.
  items.forEach(function (el) {
    var text = '';
    var sum = el.querySelector('summary');
    var ans = el.querySelector('.help-a');
    if (sum) text += sum.textContent + ' ';
    if (ans) {
      var copy = ans.cloneNode(true);
      [].forEach.call(copy.querySelectorAll('.help-rel'), function (r) { r.remove(); });
      text += copy.textContent;
    }
    el.dataset.text = text.toLowerCase();
  });

  function searching(on) {
    document.body.classList.toggle('searching', on);
  }

  // Which questions the reader had open before typing: what the search
  // opened folds again when the box is cleared, theirs stay open.
  var before = null;

  function apply(q) {
    q = q.trim().toLowerCase();
    var terms = q ? q.split(/\s+/) : [];
    var hits = 0;
    if (q && !before) {
      before = items.map(function (el) { return el.open; });
    }
    items.forEach(function (el, i) {
      var t = el.dataset.text;
      // Every word has to appear somewhere in the answer, so two words narrow
      // instead of widening - which is what people expect from a search box.
      var on = terms.every(function (w) { return t.indexOf(w) !== -1; });
      el.hidden = !on;
      if (on) hits++;
      // One letter matches nearly everything; opening all of it would be the
      // old wall of text again.
      if (q.length >= 2) el.open = on;
      else if (before) el.open = before[i];
    });
    if (!q) before = null;
    secs.forEach(function (s) {
      s.hidden = !s.querySelector('.help-item:not([hidden])');
    });
    if (count) {
      count.hidden = !q;
      count.textContent = count.dataset.fmt
        ? count.dataset.fmt.replace('%n', hits)
        : hits + ' / ' + items.length;
    }
    if (none) none.hidden = !(q && hits === 0);
    if (clear) clear.hidden = !q;
    if (toc) toc.hidden = !!q;   // the topics mean nothing while filtering
    searching(!!q);
  }

  box.addEventListener('input', function () { apply(box.value); });
  box.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') { box.value = ''; apply(''); }
  });
  if (clear) {
    clear.addEventListener('click', function () {
      box.value = ''; apply(''); box.focus();
    });
  }
  // "/" focuses the box, the way search on a documentation site usually does.
  document.addEventListener('keydown', function (e) {
    if (e.key === '/' && document.activeElement !== box
        && !/^(INPUT|TEXTAREA|SELECT)$/.test((document.activeElement || {}).tagName || '')) {
      e.preventDefault();
      box.focus();
      box.select();
    }
  });
  apply(box.value);

  // The topics were others before (1.6.4): an old link to one lands on the
  // topic its answers are in now. An answer's own id never changed.
  var MOVED = { tv: 'guide', more: 'cast', set: 'personal', local: 'library', tech: 'about' };

  // A link to one question opens it, on arrival and on a link within the
  // page (the popular questions, a related answer, the steps).
  function openTarget() {
    var id = decodeURIComponent(location.hash.slice(1));
    if (MOVED[id]) {
      id = MOVED[id];
      if (window.history && history.replaceState) history.replaceState(null, '', '#' + id);
    }
    var el = id && document.getElementById(id);
    if (!el) return false;
    if (el.classList.contains('help-item')) {
      if (box.value) { box.value = ''; apply(''); }
      el.open = true;
      el.scrollIntoView();
      return true;
    }
    if (el.classList.contains('help-sec')) {
      if (box.value) { box.value = ''; apply(''); }
      el.scrollIntoView();
      return true;
    }
    return false;
  }
  window.addEventListener('hashchange', openTarget);
  // The box takes the keys on arrival - here rather than with `autofocus`,
  // which scrolled the page back up past the question a link had opened. On
  // a phone that would also open the keyboard over the page, so not there.
  var touch = window.matchMedia && matchMedia('(hover: none), (pointer: coarse)').matches;
  if (!openTarget() && !touch) box.focus({ preventScroll: true });

  // An opened question puts itself in the address bar, so it can be linked to
  // the way the old heading links could.
  items.forEach(function (el) {
    var sum = el.querySelector('summary');
    if (!sum || !window.history || !history.replaceState) return;
    sum.addEventListener('click', function () {
      if (!el.open) history.replaceState(null, '', '#' + el.id);
    });
  });

  // The page is long enough that "where am I" is a real question, so the
  // topics mark the one you are reading (as the features page marks its
  // group). On a phone they are a row of pills: the marked one is kept in
  // sight. A short last topic never climbs as high as the band that marks
  // topics, so at the very bottom of the page it is the last one's turn.
  if (window.IntersectionObserver && tocs.length) {
    var seen = {};
    var current = null;
    var markToc = function () {
      var now = '';
      var root = document.documentElement;
      if (window.innerHeight + window.scrollY >= root.scrollHeight - 4) {
        now = secs[secs.length - 1].id;
      } else {
        secs.forEach(function (s) { if (!now && seen[s.id]) now = s.id; });
      }
      if (now === current) return;
      current = now;
      tocs.forEach(function (a) {
        if (a.dataset.toc === now) {
          a.setAttribute('aria-current', 'location');
          if (toc && toc.scrollWidth > toc.clientWidth + 4) {
            var l = a.offsetLeft - toc.clientWidth / 2 + a.offsetWidth / 2;
            toc.scrollTo({ left: Math.max(0, l), behavior: calm ? 'auto' : 'smooth' });
          }
        } else {
          a.removeAttribute('aria-current');
        }
      });
    };
    var spy = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) { seen[en.target.id] = en.isIntersecting; });
      markToc();
    }, { rootMargin: '-140px 0px -60% 0px' });
    secs.forEach(function (s) { spy.observe(s); });
    var queued = false;
    window.addEventListener('scroll', function () {
      if (queued) return;
      queued = true;
      requestAnimationFrame(function () { queued = false; markToc(); });
    }, { passive: true });
  }
})();
