/* Help page: live search, and the folded questions opened where needed.
 *
 * An EXTERNAL file, not an inline <script>. The site's CSP is
 * `script-src 'self'` with no 'unsafe-inline' (deploy/csp.snippet.txt), so an
 * inline block is silently blocked - which is exactly why the first version
 * of this search did nothing at all on the live site.
 *
 * Every topic is in the page as a <details>, so filtering is showing and
 * hiding, and what matches is opened: a hit reads without a second click.
 * A link to a question (/help/#start-linux, from the download cards) opens
 * that one. With this file absent the page is still complete - the questions
 * open by hand - just unfiltered.
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
  var toc = document.querySelector('.help-toc');

  items.forEach(function (el) {
    el.dataset.text = (el.textContent || '').toLowerCase();
  });

  function mark(on) {
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
      // Every word has to appear somewhere in the topic, so two words narrow
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
    tocs.forEach(function (a) {
      var s = document.getElementById(a.dataset.toc);
      a.hidden = !!(s && s.hidden);
    });
    if (count) {
      count.hidden = !q;
      count.textContent = count.dataset.fmt
        ? count.dataset.fmt.replace('%n', hits)
        : hits + ' / ' + items.length;
    }
    if (none) none.hidden = !(q && hits === 0);
    if (clear) clear.hidden = !q;
    if (toc) toc.hidden = !!q;   // the contents mean nothing while filtering
    mark(!!q);
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
    if (e.key === '/' && document.activeElement !== box) {
      e.preventDefault();
      box.focus();
      box.select();
    }
  });
  apply(box.value);

  // A link to one question opens it, on arrival and on a link within the page.
  function openTarget() {
    var id = decodeURIComponent(location.hash.slice(1));
    var el = id && document.getElementById(id);
    if (!el || !el.classList.contains('help-item')) return false;
    el.open = true;
    el.scrollIntoView();
    return true;
  }
  window.addEventListener('hashchange', openTarget);
  // The box takes the keys on arrival - here rather than with `autofocus`,
  // which scrolled the page back up past the question a link had opened.
  if (!openTarget()) box.focus({ preventScroll: true });

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
  // contents mark the section you are reading.
  // A short last section never climbs as high as the band that marks
  // sections, so at the very bottom of the page it is the last one's turn.
  if (window.IntersectionObserver && tocs.length) {
    var seen = {};
    var mark = function () {
      var current = '';
      var root = document.documentElement;
      if (window.innerHeight + window.scrollY >= root.scrollHeight - 4) {
        current = secs[secs.length - 1].id;
      } else {
        secs.forEach(function (s) { if (!current && seen[s.id]) current = s.id; });
      }
      tocs.forEach(function (a) {
        a.classList.toggle('is-current', a.dataset.toc === current);
      });
    };
    var spy = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) { seen[en.target.id] = en.isIntersecting; });
      mark();
    }, { rootMargin: '-80px 0px -65% 0px' });
    secs.forEach(function (s) { spy.observe(s); });
    var queued = false;
    window.addEventListener('scroll', function () {
      if (queued) return;
      queued = true;
      requestAnimationFrame(function () { queued = false; mark(); });
    }, { passive: true });
  }
})();
