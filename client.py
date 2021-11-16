import requests


def main():
    try:
        while True:
            requests.get(f"http://localhost:5000/scan/{input()}")
    except Exception:
        main()


if __name__ == "__main__":
    main()
