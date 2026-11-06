from django import template

from knowledge.readable import readable as readable_text

register = template.Library()
register.filter('readable', readable_text)
