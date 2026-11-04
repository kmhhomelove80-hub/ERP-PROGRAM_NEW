from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = 'Actas Y02·Y09 대조 자료 수집 데스크톱 버튼. 실제 재고는 변경하지 않습니다.'

    def add_arguments(self, parser):
        parser.add_argument('--directory', required=True)
        parser.add_argument('--profile', required=True)

    def handle(self, *args, **options):
        import os
        if os.name != 'nt':
            raise CommandError('Actas가 설치된 Windows 서버의 직접 로그인한 화면에서 실행하세요.')
        from inventory.actas_desktop import show_collector
        show_collector(root_folder=options['directory'], profile=options['profile'])
