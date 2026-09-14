import json

from .collect import collect


def main():
    print(json.dumps(collect(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
