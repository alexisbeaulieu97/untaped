"""Entry point for the renouveau CLI application."""

def main():
    """Main entry point that delegates to the CLI app."""
    from renouveau_app import app
    app()


if __name__ == "__main__":
    main()
