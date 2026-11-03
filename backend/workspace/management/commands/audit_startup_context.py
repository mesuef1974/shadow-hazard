import json
import uuid
from pathlib import Path
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from workspace.bootstrap import prepare_bootstrap
from workspace.models import SessionCharter
from workspace.memory import document
from workspace.startup_context import fingerprint, render, measure, validate_profile
from execution_context.routing import instruction_files


class Command(BaseCommand):
    help = 'Compare aggregate startup inputs. --measure explicitly invokes two ephemeral model requests.'

    def add_arguments(self, parser):
        parser.add_argument('--binary', required=True)
        parser.add_argument('--session', required=True)
        parser.add_argument('--handover', type=int, required=True)
        parser.add_argument('--measure', action='store_true')
        parser.add_argument('--model', default='gpt-6-luna')
        parser.add_argument('--profile', help='Opt-in user profile for the after case only')

    def handle(self, *args, **options):
        try:
            binary = Path(options['binary']).resolve(strict=True)
            charter = SessionCharter.objects.get(key=options['session'])
            bootstrap = prepare_bootstrap(charter, options['handover'])
            root = settings.BASE_DIR.parent
            paths = instruction_files()
            protected = {str(path): fingerprint(path.read_text(encoding='utf-8-sig')) for path in paths}
            rules = (charter.scope, charter.rules)
            result = {'bootstrap_receipt': bootstrap['receipt'],
                'prompt': fingerprint(bootstrap['prompt']), 'instruction_files': protected,
                'charter': fingerprint('\n'.join(rules)), 'comparison_variable': 'agents.enabled'}
            if options['profile']:
                result['profile'] = validate_profile(options['profile'])
            for name, enabled in [('before', True), ('after', False)]:
                result[name] = render(binary, root, bootstrap['prompt'], agents_enabled=enabled,
                    profile=options['profile'])
                if options['measure']:
                    if name == 'after' and not result['before']['actual']['accepted']:
                        actual = {'accepted': False, 'usage': None, 'not_run': True,
                            'failure': 'Baseline rejected; no second model request'}
                    else:
                        try:
                            actual = measure(binary, root, bootstrap['prompt'], agents_enabled=enabled,
                                model=options['model'], profile=options['profile'])
                        except ValueError:
                            actual = {'accepted': False, 'usage': None, 'failure': 'Subprocess failed; usage unknown'}
                    result[name]['actual'] = actual
                    receipt = document(charter.project, charter.key,
                        f'startup-probe:{bootstrap["receipt"]}:{name}:{uuid.uuid4()}',
                        'قياس بدء CLI عابر؛ نتيجة جزئية محفوظة دون إعادة محاولة',
                        {'variant': name, 'actual': actual, 'prompt': result['prompt'],
                         'profile': result.get('profile'), 'coverage': 'CLI only; not desktop-host savings'})
                    result[name]['actual_receipt'] = receipt.pk
            charter.refresh_from_db()
            if rules != (charter.scope, charter.rules) or protected != {
                    str(path): fingerprint(path.read_text(encoding='utf-8-sig')) for path in instruction_files()}:
                raise ValueError('Mandatory instructions or charter changed during comparison')
            result['protected_instructions_unchanged'] = True
            if options['profile'] and result['profile'] != validate_profile(options['profile']):
                raise ValueError('Comparison profile changed during measurement')
            if options['measure'] and all(result[n]['actual']['accepted'] for n in ['before', 'after']):
                before = result['before']['actual']['usage']['input_tokens']
                after = result['after']['actual']['usage']['input_tokens']
                result['cli_input_tokens_saved'] = before - after
                result['cli_reduction_percent'] = round((before - after) / before * 100, 2)
            self.stdout.write(json.dumps(result, ensure_ascii=False))
        except (ValueError, OSError, SessionCharter.DoesNotExist) as exc:
            raise CommandError(str(exc)) from exc
