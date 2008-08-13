from django.shortcuts import render_to_response
from search.models import Index

def index(request):
    query = request.GET.get('q', None)
    if query:
        index = Index()
        results = index.search(query)
    else:
        results = None

    return render_to_response('search.html', {
            'query': query,
            'results': results,
        })