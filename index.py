"""Legacy Flask entry — redirects to webapp."""

from webapp import create_app, main

app = create_app()

if __name__ == "__main__":
    main()
