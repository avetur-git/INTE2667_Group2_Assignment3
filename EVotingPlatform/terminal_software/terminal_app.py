"""Sample voting-terminal application (prototype).

In a real deployment this folder is the terminal's voting client. It is signed as
one package, so changing any file in it stops the terminal from starting."""
SOFTWARE_VERSION = '1.0.0'


def main():
    print(f'Voting terminal software {SOFTWARE_VERSION} is running.')


if __name__ == '__main__':
    main()
