from django.contrib.auth.decorators import login_required
from django.shortcuts import render


@login_required(login_url='/login/')
def dashboard(request):
    response = render(request, 'workspace/releases.html', {'release_api_root': '/releases/'})
    response['Cache-Control'] = 'private, no-cache'
    return response
