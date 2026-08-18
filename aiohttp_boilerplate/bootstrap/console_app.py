from typing import Any


class ConsoleApp:
    conf: Any = {}
    db_pool: Any = None
    loop: Any = None


def start_console_app(conf: Any, db_pool: Any, loop: Any = None) -> ConsoleApp:
    # setup application and extensions
    app = ConsoleApp()
    app.conf = conf
    app.db_pool = db_pool
    app.loop = loop
    return app
