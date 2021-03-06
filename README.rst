===============================
Django/Xappy search integration
===============================

Bridges Xappy_ (an interface to the Xapian search engine) with Django.

While other projects, like the `GSoC 2008 project
<http://code.google.com/p/djangosearch/>`_ try to be generic
and support a common set of functionality, this allows you to take
full advantage of the features provided by Xappy. On the downside, it
is Xappy-specific.


Status
======

This has not been worked on for a while; Neither has the Xappy_ library this
is based on. You may want to have a look at `django-haystack`_,
which also supports Xapian.

This probably won't change. I quite like Haystack, and I would likely
prefer to work on exposing more native Xapian features via the Haystack
API, where required.

Still, I'll happily merge patches to this repository, even if I'm not
at this time working on it myself.


.. _Xappy: http://code.google.com/p/xappy/
.. _django-haystack: http://haystacksearch.org/


Dependencies
============

Just Python 2.5, Django and Xappy. Xappy should be a recent version,
the app is currently written against revision 252.


Usage
=====

.. admonition:: Note

    Don't forget to familarize yourself with Xappy first:
    http://code.google.com/p/xappy/source/browse/trunk/docs/

django-xappy was originally designed for a project with an index
spanning multiple models. As such, keep in mind that if you're use case
is simpler, usage may currently not be as straightforward and easy as
it could be.

In the case that one index does include multiple models, the official
Django search-api branch, as well as some other projects, for example
``djapian``, use a proxy model that mirrors all documents in the index.
For a example, see:

    http://code.google.com/p/djapian/wiki/IndexingManyModelsAtOnce

We adapt that approach, however, instead of maintaining an additional
model with it's own rows duplicating all other models, the proxy is
simply a non-model object that defines the fields of the index, and to
what fields of each particular models they map.

Defining an index
-----------------

The first step is to define the index. This primarily entails the fields
that the index is supposed to have, and the Xappy actions to apply to
each field::

    import django_xappy as search
    from django_xappy import action, FieldActions

    class MyIndex(search.Index):
        location = '/var/search/index'

        class Data:
            @action(FieldActions.INDEX_FREETEXT)
            def name():
                return "index this!"

First, note that we specify the location attribute directly in the class.
This may seem counter-intuitive at first if you expect that to be
instance data, but note that your index class is not a template for just
some index, but, like each model represents a database table, it
represents an actual physical search index that you intend to maintain.

Now, every method of the inner ``Data`` class that has at least one
action applied to it is considered a field of the index.

Remember that while an index can store the content of multiple models
with clashing field names, it's own field names must be unique. For this
reason, you define fields as methods and return the appropriate value for
the model instance in ``self.content_object`` (your ``Data`` class is
the proxy that wraps around the objects to be indexed).

Example::

    @action(FieldActions.INDEX_FREETEXT)
    @action(FieldActions.STORE_CONTENT)
    def name(self):
        if self == Book:
            return self.content_object.title
        elif self == auth.User:
            return self.content_object.username

This field is supposedly part of an index that searches both ``Books``
and ``Users``. It maps to ``Book.title`` or ``User.username``, depending
on the type of an object.

Registering the models
----------------------

Once your index is defined, you must tell it which models it handles.
Note that a model can be registerd with multiple indexes.

::

    MyIndex.register(Book)
    MyIndex.register(auth.User)

This will cause all changes to those model are logged, so make sure it
runs before you start working with any of the affected models.

Putting it in an app's ``models.py`` file works best. For larger
projects I usually create a separate ``search`` application with it's
own ``models.py`` file, and define the index there.

Alternatively, using an application's ``__init__.py`` works as well.

Using the index
---------------

To connect to your index, simply create an instance:

    index = MyIndex()

.. admonition:: Note

    If you want to open your index at a location other than the default,
    the following works as well:

        index = MyIndex('/some/other/place')

    Just remember that django-xappy's own code will always open the
    default location (for example, the update code), so this is really
    only useful in rare cases.

To search, just do::

    results = index.search('who am i')

This will give you the first ten results.

::

    results = index.search('who am i', page=3, num_per_page=5)

Now, the result set includes 5 documents from page 3.

See the **Advanced Usage** section for more about pagination.

.. admonition:: Note

    You can also modify the index, although you usually don't need to
    (and shouldn't) do this. Use the provided update scripts instead.
    For example, to add a document::

        f = Film.objects.get(pk=1)
        index.add(f)
        index.flush()

.. admonition:: Note

    The Xappy separation between a search and an indexer connection is
    hidden by the index class, although if possible you should only use
    an instance for either modifying or searching.

In templates
------------

Usually, you would pass the results collection that is returned by
``search()`` into your template.

There, you can simply iterate over it::

    {% if results %}
        {% for result in results %}
            {{ result.content_object }}
        {% endif %}
    {% endif %}

``result.content_object`` gives you access to the orignal model
instance. If you used the STORE_CONTENT action on some of your
fields, you may instead those values using on of::

    {{ result.some_field }}
    {{ result.highlighted.some_field }}
    {{ result.summarised.some_field }}

Keeping your index up-to-date
-----------------------------

Since django-xappy logs all changes to your models instead of applying
them directly, you need to update your index in regular intervals.

A management command is available to help you with this. Provided you
have **django-xappy** in your ``INSTALLED_APPS`` list, you can do::

    $ ./manage.py index --update

for an incremental update, and

::

    $ ./manage.py index --full-rebuild

to rebuild all indexes from scratch.

To apply changes on a regular basis, you normally would just setup a
cronjob to run ``manage.py index --update -q``.

.. admonition Note on using multiple indexes

    Due to the way the model change log is stored (with only one
    record per change), it is currently not possible to update
    indexes selectively. There is no way to track which change has
    already been applied to which index.


Advanced usage
==============

Complex search queries
----------------------

So far, we always passed a query string to ``Index.search()``, which was
then internally resolving using Xappy's ``query_parse()``. If you need
more  control, you can manually build a Query object and give that to
the search method. All of Xappy's query builders are exposed by the
index.

For example, say you want to restrict the user's search to results from
a certain category::

	q = index.query_parse(request.GET.get('q'))
	q = index.query_filter(
			index.query_field('category', request.GET.get('cat'))
	)

	results = index.search(q, query_str=request.GET.get('q'))

Note that ``query_filter`` differs from an AND-``query_composite`` in
that only the first part of the query is used for ranking purposes. See
the Xappy docs for more information.

Further note that in addition to the Query object we built we also pass
the ``query_str`` parameter to ``search()``. This is required so that
the query can be spell checked and a corrected version made available.
If you don't pass ``query_str``, the spell checked version will not be
available on the ``results`` object (although you are free to call
``index.spell_correct`` manually).

Pagination
----------

While technically, you have to use pagination (the ``search()`` function
always returns a paged subset of the results), there currently isn't good
support for pagination with respect to display, i.e. rendering **next**
and **previous** links etc.

You can however use an external paginator to do this, like the one that
Django has builtin::

    from django.core.paginator import Paginator
    Paginator(results, num_per_page).page(page)

Just make sure that the ``num_per_page`` and ``page`` values are the same
that you passed into ``search()``.

Multiple field values
---------------------

Sometimes, you may want to add a field multiple times to the index, for
example, if you are using the TAG action. To do this, simply make your
data function a generator::

	class Data:
		@action(FieldActions.TAG)
		def tags(self):
			for tag in self.content_object.tags:
				yield tag.name

Partial model registration
--------------------------

Rather than registering a full model, you can also just pass a queryset
to ``register``::

    MyIndex.register(Book.objects.all(is_public=True))

This will ensure that only ``Book`` objects that match the given query
will end up in the index. As you can see in the example, this can be
useful e.g. for excluding private objects from the index. Note however,
that while updating the public status of an existing object to True will
make the object appear in the index due to "add" and "update" being
synonymous, switching an existing object to be private would not delete
it from the index. This may improve in the future (see also TODO section).

Custom update scripts
---------------------

If you don't like to use the management command, you can create a
standalone update script. A default script is provided that you
can easily wrap around::

    # 1) SETUP DJANGO
    ...

    # 2) RUN SCRIPT
    from django_xappy.scripts import update
    update.main()

Keep in mind that you **have** to do step 1 and setup your project's
Django environment for this script. For information on how to do this,
see:

    http://www.b-list.org/weblog/2007/sep/22/standalone-django-scripts/

Also, all modules that define an index need to be loaded, or
``update.main`` won't know **what** to update.

``examples\simple\scripts\update_index.py`` shows how this might look.

If you want to further customize things: ``update.main`` wraps around
the lower-level functions ``apply_changes`` and ``rebuild``, which you
can call directly. Of course, you can also manually modify the index as
per your liking, using ``index.update()``, ``index.delete()`` etc.

OpenSearch
----------

Limited functionality to work with OpenSearch is included.

For more information about OpenSearch, see::

    http://www.opensearch.org/
    http://www.opensearch.org/Specifications/OpenSearch/1.1

In ``django_xappy.feeds`` you will find a subclass of Django's own
``syndication.Feed`` that can be used to output a feed for your search
results, while adding the OpenSearch response metadata. You basically
use it like the default ``Feed`` class, defining what data to include
in titles, descriptions etc., with the following specialties:

    * No need to define ``items`` - this will use the list of search
      result automatically.

    * Instead, you need to define ``results``, pointing it to a
      django-xappy search results objects.

    * Optionally, you may set ``spell_suggestion`` to False if you do
      not want to include a spelling correction in the metadata, even
      if would be available.


Incompatible Changes
====================

After 0.1
---------

Revision 19:
	``order_by`` parameter to ``search()`` no longer exists, use the
	Xappy original ``sortby``.

TODO
====
    * Simplify usage for simple cases where an index does not
      spawn multiple models.
    * Port tests from critify project, pay particular attention to
      model inheritance issues.
    * Fail if a data class does not define any fields/actions?
    * Add a "search" management command for some simple index testing.
    * Allow disabling of search result database resolving - when
      outputting the search results, instead of using a resolved model
      instance, one would have to use STORE_CONTENT index fields
      instead. On the plus side, performance would likely improve.
    * Improve the example project with respect to search display (
      model-specific results, result highlighting, ...)
    * Better pagination features. There is no reason why one would have
      to use an external paginator.
    * Support accent normalization (see src/djapian/backend/text.py)
    * When not using a queryset restriction, then during index rebuild,
      model.objects.all() will be used, which may be a custom manager
      with a restrictive default query, while a partial update essentially
      truly handles **all** objects. Both cases should behave the same.
    * If an object is updated, and the update removes it from the queryset
      it's model used to register with the index, the object will not be
      removed from the index; this **could** be done automatically though
      by checking with the queryset during the save-signal handler and
      logging a "delete" change. It would also cost performance though, so
      maybe this should be optional behaviour.