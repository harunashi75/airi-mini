import argparse
import logging
from core.brain import Brain
from core.i18n import set_language, t
from core.settings import load_settings
from core.suggestions import MemorySuggestion
from core.storage import lock_data_directory


def main():
    try:
        set_language(load_settings().language)
    except Exception:
        pass  # Brain() reports configuration errors properly below.
    parser = argparse.ArgumentParser(description=t("cli.description"))
    parser.add_argument("--session", help=t("cli.session_help"))
    parser.add_argument("--list", action="store_true", help=t("cli.list_help"))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if args.list:
        from core.sessions import SessionStore
        for session in SessionStore().list():
            print(session['id'], session['title'])
        return
    try:
        with lock_data_directory():
            chat(args.session)
    except (KeyboardInterrupt, EOFError):
        print("\n" + t("cli.closing"))
    except Exception as exc:
        logging.error(t("cli.start_failed", error=exc))
        raise SystemExit(1) from exc


def chat(session_id):
    print(t("cli.loading"))
    brain = Brain()
    try:
        session = brain.sessions.get(session_id) if session_id else brain.sessions.create()
        print(t("cli.welcome", id=session.id))
        prompt = t("cli.prompt", user=brain.user_name) if brain.user_name else t("cli.prompt_anonymous")
        exit_words = t("cli.exit_words").split()
        while True:
            text = input(prompt).strip()
            if text.casefold() in exit_words:
                break
            if not text:
                continue
            try:
                print("\n" + t("cli.reply", assistant=brain.assistant_name), end="", flush=True)
                for chunk in brain.stream(text, session.id):
                    if isinstance(chunk, MemorySuggestion):
                        print("\n\n" + t("cli.suggestion", fact=chunk.fact), end="", flush=True)
                    else:
                        print(chunk, end="", flush=True)
                print("\n")
            except Exception as exc:
                logging.error(t("cli.exchange_failed", error=exc))
    finally:
        brain.close()


if __name__ == "__main__":
    main()
