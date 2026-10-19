"""Application entry point: ``python -m nexus_remote.main`` or ``nexus-remote``."""

from __future__ import annotations

import argparse
import logging
import sys
import traceback
from pathlib import Path

from nexus_remote import config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=config.APP_SLUG,
        description=f"{config.APP_NAME} - one workspace for remote access and device management",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="override the local data directory (database, logs, exports)",
    )
    parser.add_argument("--version", action="store_true", help="print the version and exit")
    parser.add_argument(
        "--selftest",
        action="store_true",
        help="initialise storage and print an inventory summary without starting the GUI",
    )
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv if argv is not None else sys.argv[1:])
    if args.version:
        print(f"{config.APP_NAME} {config.APP_VERSION}")
        return 0

    if args.data_dir:
        import os

        os.environ[config.HOME_ENV_VAR] = str(args.data_dir)

    from nexus_remote.services import build_services, configure_logging

    paths = config.default_paths().ensure()
    log = configure_logging(paths, level=getattr(logging, args.log_level))
    log.info("starting %s %s (data dir: %s)", config.APP_NAME, config.APP_VERSION, paths.root)

    try:
        services = build_services(paths)
    except Exception as exc:  # pragma: no cover - startup failure path
        log.exception("could not initialise local storage")
        print(f"could not initialise {config.APP_NAME}: {exc}", file=sys.stderr)
        return 1

    if args.selftest:
        environments = services.devices.count_by_environment()
        print(f"{config.APP_NAME} {config.APP_VERSION}")
        print(f"data directory : {paths.root}")
        print(f"database       : {paths.database}")
        print(f"fts5 available : {services.database.fts_enabled}")
        print(f"vault backend  : {services.credentials.store.backend_name}")
        print(f"devices        : {services.devices.count()} {environments or ''}")
        print(f"audit events   : {services.audit.count()}")
        print(f"history rows   : {services.history.stats().get('total', 0)}")
        print(f"adapters       : {', '.join(services.sessions.available_protocols()) or 'none registered'}")
        services.close()
        return 0

    from PySide6.QtWidgets import QApplication, QMessageBox

    from nexus_remote.ui.main_window import MainWindow
    from nexus_remote.ui.theme import apply_theme

    application = QApplication(sys.argv[:1])
    application.setApplicationName(config.APP_NAME)
    application.setOrganizationName(config.ORG_NAME)
    application.setApplicationVersion(config.APP_VERSION)
    apply_theme(application)

    def handle_exception(exc_type, exc_value, exc_traceback) -> None:
        if issubclass(exc_type, KeyboardInterrupt):  # pragma: no cover
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        log.error("unhandled exception: %s", "".join(traceback.format_exception(exc_type, exc_value, exc_traceback)))
        try:
            QMessageBox.critical(
                None,
                "Unexpected error",
                f"{exc_value}\n\nThe details were written to:\n{paths.logs / 'nexus-remote.log'}",
            )
        except Exception:  # pragma: no cover - GUI may already be gone
            pass

    sys.excepthook = handle_exception

    window = MainWindow(services)
    window.show()
    exit_code = application.exec()
    services.close()
    return int(exit_code)


if __name__ == "__main__":
    raise SystemExit(main())
