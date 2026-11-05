from django import template

register = template.Library()


@register.filter
def tokens(value):
    """Short reading form: 12,345,678 → «12.3 مليون»."""
    try:
        value = int(value or 0)
    except (TypeError, ValueError):
        return 'غير متاح'
    if value >= 1_000_000:
        return f'{value / 1_000_000:.1f} مليون'
    if value >= 10_000:
        return f'{value / 1_000:.1f} ألف'
    return f'{value:,}'


@register.filter
def exact(value):
    try:
        return f'{int(value or 0):,}'
    except (TypeError, ValueError):
        return 'غير متاح'
