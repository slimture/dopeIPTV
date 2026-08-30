/* Help page: live search.
 *
 * An EXTERNAL file, not an inline <script>. The site's CSP is
 * `script-src 'self'` with no 'unsafe-inline' (deploy/csp.snippet.txt), so an
 * inline block is silently blocked - which is exactly why the first version
 * of this search did nothing at all on the live site.
 *
 * Every topic is already in the page with its answer visible, so filtering is
 * showing and hiding, and a match reads immediately without a second click.
 * With this file absent the page is still complete, just unfiltered.
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

  function apply(q) {
    q = q.trim().toLowerCase();
    var terms = q ? q.split(/\s+/) : [];
    var hits = 0;
    items.forEach(function (el) {
      var t = el.dataset.text;
      // Every word has to appear somewhere in the topic, so two words narrow
      // instead of widening - which is what people expect from a search box.
      var on = terms.every(function (w) { return t.indexOf(w) !== -1; });
      el.hidden = !on;
      if (on) hits++;
    });
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

  // The page is long enough that "where am I" is a real question, so the
  // contents mark the section you are reading.
  if (window.IntersectionObserver && tocs.length) {
    var seen = {};
    var spy = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) { seen[en.target.id] = en.isIntersecting; });
      var current = '';
      secs.forEach(function (s) { if (!current && seen[s.id]) current = s.id; });
      tocs.forEach(function (a) {
        a.classList.toggle('is-current', a.dataset.toc === current);
      });
    }, { rootMargin: '-80px 0px -65% 0px' });
    secs.forEach(function (s) { spy.observe(s); });
  }
})();
