"""Provides read functionality to the search index, i.e. per performing
queries.

See ``SearchResults._mkresult`` for information on how we handle
desync-issues between the index and the database.
"""

# encoding: utf-8
import time
import xappy
from django.conf import settings
from django.utils.safestring import mark_safe
from django.contrib.contenttypes.models import ContentType
from djutils import template_callable


class XapianSearcher(object):
    """Light wrapper around a Xapian index.

    Late binding ensures that the index is only opened when necessary.
    """

    def __init__(self):
        self._connection = None

    @property
    def path(self):
        return settings.SEARCH_INDEX_LOCATION

    def __connect(self):
        """
        Ensure a xappy connection exists. This makes sure we connect only once,
        but also only when necessary; requests that do not need search will
        never have to touch the index.
        """
        if not self._connection:
            self._connection = xappy.SearchConnection(self.path)

    @property
    def length(self):
        """Return total number of documents in the index."""
        self.__connect()
        self._connection.get_doccount()

    def search(self, query, page=1, num_per_page=10, order_by=None,
               type_filter=None):
        """
        Does a search. Note that instead of specifying a start- and end-index,
        you pass the ``page`` (1-based), and the number of items per page
        (``num_per_page``) to this method. Not only will the caller have
        less work to do, it also makes it easier for us to return a fixed
        page number:

        Because Xapian does not always provide an exact estimate, code that
        uses a paginator when outputting the page might show pages that do not
        actually exist. If the user then requests on of those, we couldn't show
        any results - the right thing would be to gracefully display the last
        page of results. This method tries to detect this situation, and to
        report the fixed page number back to the caller, it always returns a
        2-tuple (results, page_num).

        ``type_filter`` is a restriction that will be placed on the ``type``
        field in the index, referring to type of info indexed (game, book,
        review, ...)

        Pass ``False`` to ``order_by``, if you want the default sort order by
        relevance.
        """

        # convert the page number given into a start offset
        start = (page-1)*num_per_page
        count = num_per_page
        # xapian requires us to use utf8, but we need the original as well
        query_utf8 = query.encode('utf-8')

        # prepare connection
        self.__connect()
        conn = self._connection

        # start timer
        ts_begin = time.time()

        # build the xapian query
        q = conn.query_parse(query_utf8)
        if type_filter:
            q = conn.query_filter(q, conn.query_field('type', type_filter))

        # we might have to do the search twice with different start indices,
        # so use a simple lambda.
        __search = lambda s:\
            conn.search(q, s, s+count, sortby=order_by, getfacets=True)
        # first, attempt a normal search, which works in most cases
        results = __search(start)
        # However, because xapian does not always return an exact hit count,
        # a paginator on the client side might show more pages than actually do
        # exist, and this function ends up being called with a too large page
        # number. Instead of not returning anything, or failing, we would like
        # to handle this case gracefully and return the last page. So here is
        # where we try to detect this case, and repeat the search, requesting
        # the actual last page of results. Note that this is only possible
        # because in this case we now DO have the exact number of hits, because
        # Xapian went through the whole resultset on the search we just did.
        if results.matches_estimated<start+1 and results.estimate_is_exact:
            page = (results.matches_estimated/count)+1
            start = (page-1)*num_per_page
            results = __search(start)

        # timer done
        search_time = time.time() - ts_begin

        # check for misspellings, if we can find something better
        spell_suggestion = conn.spell_correct(query_utf8)
        spell_suggestion = (spell_suggestion!=query_utf8 and
                               [spell_suggestion.decode('utf-8')] or  # store as unicode
                               [None])[0]

        # return the results wrapped in our custom class, and the possibly
        # changed page number, as a tuple
        return XapianResults(
                    results,
                    offset=start,
                    query=query,
                    search_time=search_time,
                    spell_suggestion=spell_suggestion),\
               page

    def close(self):
        if self._connection:
            self._connection.close()
            self._connection = None
    def reopen(self):
        self.close()
        self.__connect()


class XapianResults(object):
    """
    A thin wrapper around the ``SearchResults`` object returned by Xappy,
    exposing the functionality we care about most, and also wrapping each
    result in a ``XapianResult`` class.

    The number in ``offset`` specifies the first index of the search results.
    If the query requested the results 1-10, offset will be 0, for the results
    31-40, offset will be 340. This is because we have to access them 0-index
    based (see the ``__getitem__`` method for more information).
    """
    def __init__(self, results, offset, query,
                 search_time=None, spell_suggestion=None):
        self._results = results
        self.offset = offset
        self.query = query
        self.search_time = search_time
        self.spell_suggestion = spell_suggestion

        # build an html version of the spell suggestion that has the
        # changed terms highlighted.
        if spell_suggestion:
            query_words = query.split(' ')
            fixed_words = spell_suggestion.split(' ')
            with_changed = []
            for word1, word2 in zip(query_words, fixed_words):
                if word1 != word2: fstr = '<strong><em>%s</em></strong>'
                else: fstr = '%s'
                with_changed.append(fstr%word2)
            self.spell_suggestion_html = mark_safe(" ".join(with_changed))

    def _mkresult(self, index):
        """
        Wrap a xappy search result with our own custom class. In addition, this
        method serves two important functions:

        Firstly, it resolves each search results to a model instance, which it
        makes available via the ``content_object`` attribute. This is done in
        a smart way to reduce the number of queries, fetching multiple items
        in one go.

        Also, this is the place were we handle sync issues between the database
        and the search index - an object might already be deleted from
        the database while the index has not yet been updated. Our current
        solution is just to ignore and skip the result, so a result page might
        end up not actually containing less items than it's supposed to.
        A possibly better solution would be to build the result page only with
        data from the index, but that is probably considerably more work, as
        we cannot use our normal utiltity functions, or would have to update
        them.

        ``index`` is normally the 0-based index to be used with ``get_hit``,
        but can also be an already retrieved ``SearchResult`` object.
        """
        # TODO: get all content_object of same type instances at once
        if isinstance(index, xappy.SearchResult): result = index
        else: result = self._results.get_hit(index)
        try:
            content_object = ContentType.objects.get(
                pk=result.data['ctype_id'][0]).get_object_for_this_type(pk=result.data['obj_id'][0])
        except:
            return False
        else:
            return XapianResult(result, content_object)

    def __iter__(self):
        for result in self._results:
            yield self._mkresult(result)

    def __getitem__(self, key):
        """
        Allow direct access to search results, even slice based. Our pagination
        code requires this, but it is a bit tricky since we have only the
        explicitly requested results available in a 0-indexed fashion, but the
        calls to this function will use the real index (results 50-60
        accessible via index 0-9, but requested within this function is 49-59).
        """
        if isinstance(key, int):
            return self._mkresult(index-self.offset)
        elif isinstance(key, slice):
            result = []
            for i in range(*key.indices(self.count)):
                try: result.append(self._mkresult(i-self.offset))
                except IndexError: pass
            return result
        else:
            raise KeyError("key must be an int or slice")

    def __len__(self):
        """
        This returns the exact number of items, or as closely as possible, for
        internal use when building a pagination for example (as opposed to
        the ``count`` method.
        """
        return self._results.matches_estimated

    @property
    def count(self):
        """
        This returns the number of items suggested for display (as opposed to
        the ``__len__`` implementation).
        """
        return self._results.matches_human_readable_estimate
    @property
    def count_is_estimated(self):
        return not self._results.estimate_is_exact

    @property
    def type_suggestions(self):
        """
        Return suggested type restrictions (e.g. restrict search to
        reviews, games, persons...); Currently implemented using facets.
        """
        # do we need to cache get_suggested_facets()? probably not...
        facets = self._suggested_facets =\
            dict(self._results.get_suggested_facets())
        return facets.get('type')


class XapianResult(object):
    """Simple wrapper around a Xappy ``SearchResult`` object that
    provides certain functions we like/need to use in templates.
    """

    def __init__(self, result, content_object):
        self._result = result
        self.content_object = content_object

    def __getattr__(self, name):
        try:
            return getattr(self._result, name)
        except AttributeError:
            try:
                return self._result.data[name][0]
            except IndexError:
                raise AttributeError()

    @template_callable
    def highlighted(self, field):
        return mark_safe(self._result.highlight(field)[0])

    @template_callable
    def summarised(self, field):
        return mark_safe(self._result.summarise(field, maxlen=180))

    @property
    def model(self):
        """
        Return the model name as a string; This is for use in templates:
            {% ifequal result.model "review" %}...
        Note that it returns "baseitem", not the subtype.
        """
        return type(self.content_object).__name__.lower()


index = XapianSearcher()