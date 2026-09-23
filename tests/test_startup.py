"""Startup path selection without opening a server, database or provider."""

from pathlib import Path
import unittest
from unittest.mock import patch

import app


class StartupTests(unittest.TestCase):
    def run_main(self, arguments, defaults):
        with patch('sys.argv', ['app.py', '--state-dir', 'isolated-state', *arguments]), \
                patch('app.default_paths', return_value=defaults) as discover, \
                patch('app.Application') as application, \
                patch('app.ThreadingHTTPServer') as server, \
                patch('builtins.print'):
            app.main()
        server.assert_called_once_with(('127.0.0.1', 8765), app.Handler)
        self.assertIs(server.return_value.application, application.return_value)
        server.return_value.serve_forever.assert_called_once_with()
        return application, discover

    def test_no_auto_load_starts_empty_without_discovery_or_background_loading(self):
        for arguments in (['--no-auto-load'], ['--archives', '--no-auto-load']):
            with self.subTest(arguments=arguments):
                application, discover = self.run_main(arguments, [Path('unused-default.zip')])
                application.assert_called_once_with([], 'isolated-state')
                discover.assert_not_called()
                application.return_value.submit.assert_not_called()
                application.return_value.load.assert_not_called()

    def test_explicit_archives_override_no_auto_load_and_are_submitted_once(self):
        expected = [Path('chosen-one.zip'), Path('chosen-two.zip')]
        application, discover = self.run_main(
            ['--no-auto-load', '--archives', *map(str, expected)], [Path('unused-default.zip')])
        application.assert_called_once_with(expected, 'isolated-state')
        discover.assert_not_called()
        application.return_value.submit.assert_called_once()
        application.return_value.submit.call_args.args[0]()
        application.return_value.load.assert_called_once_with(expected)

    def test_default_start_preserves_default_archive_loading(self):
        expected = [Path('default-one.zip'), Path('default-two.zip')]
        application, discover = self.run_main([], expected)
        application.assert_called_once_with(expected, 'isolated-state')
        discover.assert_called_once_with()
        application.return_value.submit.assert_called_once()
        application.return_value.submit.call_args.args[0]()
        application.return_value.load.assert_called_once_with(expected)


if __name__ == '__main__':
    unittest.main()
