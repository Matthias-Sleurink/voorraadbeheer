import requests

if __name__ == "__main__":
    while True:
        requests.get(f"http://localhost:5000/scan/{input()}")
