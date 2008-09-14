"""Provides tools and utilities for creating search-related feeds,
e.g. OpenSearch.
"""

from django.contrib.syndication.feeds import Feed as BaseFeed, FeedDoesNotExist
from django.utils.feedgenerator import Atom1Feed, Rss201rev2Feed


__all__ = ('OpenSearchFeed',)


class OpenSearchFeedBase(object):
    """Feedgenerator mixin class that adds OpenSearch data.

    Combine with a feedgenerator class.
    """

    def root_attributes(self):
        attrs = super(OpenSearchFeedBase, self).root_attributes()
        attrs[u'xmlns:opensearch'] = u'http://a9.com/-/spec/opensearch/1.1/'
        return attrs

    def add_root_elements(self, handler):
        super(OpenSearchFeedBase, self).add_root_elements(handler)

        def add(tag, value=None, attrs=None):
            handler.addQuickElement(u'opensearch:%s' % tag,
                (value is not None) and u"%s" % value or value,
                attrs=attrs)

        results = self.feed.get('results')
        add(u'totalResults', len(results))
        add(u'startIndex', (results.offset+1))
        add(u'itemsPerPage', results.num_per_page)

        # TODO: a startPage attribute sould probably be included in most
        # cases to allow full recreation of the query.
        add(u'Query', attrs={'role': 'request', 'searchTerms': results.query})
        if self.feed.get('spell_suggestion') and results.spell_suggestion:
            add(u'Query', attrs={'role': 'correction',
                                 'searchTerms': results.spell_suggestion})

        # TODO: add a self-reference to description file
        # <atom:link rel="search" type="application/opensearchdescription+xml" href="http://example.com/opensearchdescription.xml"/>


class OpenSearchRSSFeed(OpenSearchFeedBase, Rss201rev2Feed):
    """RSS version of the feedgenerator.
    """
    pass


class OpenSearchAtom1Feed(OpenSearchFeedBase, Atom1Feed):
    """Atom version of the feedgenerator.
    """
    pass


class OpenSearchFeed(BaseFeed):
    """A syndication feed subclass that uses the OpenSearch spec to
    publish information about a search result.

    User must define a ``results`` attribute on the feed class that
    points to a django-xappy search result object.

    By default, an OpenSearch Query element with role="correction"
    will be included with the spelling suggestion. You may disable
    this by setting a ``spell_suggestion`` to False.

    See also:
        http://www.opensearch.org/Specifications/OpenSearch/1.1
    """

    feed_type = OpenSearchRSSFeed
    spell_suggestion = True

    results = None   # specified by subclass

    def __init__(self, *args, **kwargs):
        super(OpenSearchFeed, self).__init__(*args, **kwargs)

    def items(self):
        return iter(self.results)

    def feed_extra_kwargs(self, obj):
        return {'results' : self.results,
                'spell_suggestion': self._Feed__get_dynamic_attr('spell_suggestion', True)}