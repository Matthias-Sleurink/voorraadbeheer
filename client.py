import requests
import socket


# thanks SO: https://stackoverflow.com/a/28950776
def get_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    # noinspection PyBroadException
    try:
        # doesn't even have to be reachable
        s.connect(("10.255.255.255", 1))
        IP = s.getsockname()[0]
    except Exception:
        IP = "127.0.0.1"
    finally:
        s.close()
    return IP


def main():
    ip = get_ip()

    while True:
        # noinspection HttpUrlsUsage
        print(requests.get(f"http://{ip}:5000/scan/{input()}").text)


if __name__ == "__main__":
    while True:
        # noinspection PyBroadException
        try:
            main()
        except Exception:
            # The scanner sometimes gives us an EOF, after which we wanna reopen the input() waiter. we do however,
            # want to bail out on sigint etc. Those are not Exceptions but other raise-ables so not caught here.
            continue
