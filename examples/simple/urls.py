from django.conf.urls.defaults import *

from django.contrib import admin
admin.autodiscover()

urlpatterns = patterns('main.views',
    url(r'^admin/(.*)',      admin.site.root, name="admin-root"),
    url(r'^$',              'index'),
)
