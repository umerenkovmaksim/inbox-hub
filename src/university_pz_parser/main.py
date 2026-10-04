from datetime import datetime

from university_pz_parser.settings import get_settings
from university_pz_parser.sources.telegram.parser import TelegramParser


def main():
    settings = get_settings()
    parser = TelegramParser(settings.telegram)
    print(
        parser.parse_messages(
            settings.telegram.source_ids, date_from=datetime(2026, 9, 1)
        )
    )


if __name__ == "__main__":
    main()
