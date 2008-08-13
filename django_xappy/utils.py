__all__ = (
    'template_callable',
)


def template_callable(func):
    """Let a function be called with exactly **one** argument from a
    Django template, by passing it via an attribute:

        class SearchResult(object):
            @template_callable
            def highlighted(self, field):
                return self._xappy_result.highlight(field)

        result = SearchResult()
        result.highlighted.title

    Then:

        {{ result.highlighted.title }}

    """
    class GetAttrCaller(object):
        def __init__(self, instance):
            self.instance = instance
        def __getattr__(self, name):
            return func(self.instance, name)
        # Would cause Django to think this is a method, even through templates
        #def __call__(self, *args, **kwargs):
        #    return func(self, *args, **kwargs)
    class TemplateCallableDescriptor(object):
        def __get__(self, instance, klass):
            return GetAttrCaller(instance)
    return TemplateCallableDescriptor()