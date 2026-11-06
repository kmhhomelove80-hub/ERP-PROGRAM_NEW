"""Read-only probes plus explicit backup/migration commands for a code update."""
import json
import os
from pathlib import Path
import runpy
import sys
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django
django.setup()
from django.db import connection

mode = sys.argv[1]
if mode == 'profile':
    from django.conf import settings
    connection.ensure_connection()
    db = settings.DATABASES['default']
    size = 0
    if connection.vendor == 'postgresql':
        with connection.cursor() as cursor:
            cursor.execute('SELECT pg_database_size(current_database())')
            size = cursor.fetchone()[0]
    print(json.dumps({'vendor': connection.vendor, 'name': db['NAME'], 'user': db.get('USER', ''), 'host': str(db.get('HOST', '')), 'size': size}))
elif mode == 'snapshot':
    runpy.run_path(str(Path(__file__).with_name('update_snapshot.py')))
elif mode == 'plan':
    from django.db.migrations.executor import MigrationExecutor
    executor = MigrationExecutor(connection)
    print(json.dumps([[m.app_label, m.name, backwards] for m, backwards in executor.migration_plan(executor.loader.graph.leaf_nodes())]))
elif mode == 'backup':
    from inventory.backups import create_backup, verify_backup
    folder = create_backup(sys.argv[2], sys.argv[3])
    verify_backup(folder)
    print(json.dumps({'path': str(folder)}))
elif mode == 'health':
    from django.contrib.auth import get_user_model
    from django.contrib.messages.storage.fallback import FallbackStorage
    from django.test import RequestFactory
    from django.urls import resolve
    user = get_user_model().objects.filter(is_active=True).order_by('pk').first()
    if user is None:
        raise RuntimeError('No active ERP account for read-only rendering check')
    paths = ['/', '/receipts/', '/allocations/', '/movements/']
    if '--old' not in sys.argv:
        paths.extend(['/production/','/inbound/saved/','/company-print/'])
    for path in paths:
        host = sys.argv[sys.argv.index('--host')+1] if '--host' in sys.argv else '127.0.0.1'
        request = RequestFactory().get(path, HTTP_HOST=host)
        request.user = user
        request.session = {}
        request._messages = FallbackStorage(request)
        match = resolve(path)
        request.resolver_match = match
        response = match.func(request, *match.args, **match.kwargs)
        if response.status_code != 200:
            raise RuntimeError('Read-only page render failed: ' + path)
    print(json.dumps({'read_only_pages': paths}))
else:
    raise RuntimeError('Unknown update probe')
