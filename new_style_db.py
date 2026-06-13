import json
import subprocess
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple, Any

from flask import Flask, redirect, request, jsonify, render_template
from sqlalchemy import Column, Integer, String, Text, create_engine, Engine
from sqlalchemy.orm import declarative_base, sessionmaker, Session

UNKNOWN_STORE_ID = 1

Base = declarative_base()

newstyle_app = Flask(__name__)
session_maker: sessionmaker


def make_engine():
    return create_engine(
        "sqlite:///" + str(Path(".") / "voorraad_newstyle.db")
        , echo=True
    )  # For debug echo=True


def create_database() -> Engine:
    engine = make_engine()
    Base.metadata.create_all(engine, tables=[Setting.__table__,
                                             Barcode.__table__,
                                             Store.__table__,
                                             Product.__table__])
    # Re-create the engine to ensure we have a fresh new connection to the DB.
    engine.dispose()
    return make_engine()


class Setting(Base):
    __tablename__ = "setting"
    name: str = Column(String(128), primary_key=True)
    value: str = Column(Text(), nullable=False)


class Settings:
    emails = "emails"
    scan_action = "scan_action"


class ScanActions:
    increase = "increase"
    decrease = "decrease"

    @staticmethod
    def is_add_barcode(action: str) -> bool:
        return action.startswith("add_barcode+")

    @staticmethod
    def add_barcode(current_action: str, barcode: str) -> str:
        return f"add_barcode+{barcode}->{current_action}"

    @staticmethod
    def split_add_barcode(action: str) -> Optional[Tuple[str, str]]:
        """
        Split the add barcode action into a tuple [barcode, previous_action]
        :param action: a scan action made by add_barcode(str, str).
        :return: None of the format is not right, [barcode, previous_action] if it is.
        """
        if not ScanActions.is_add_barcode(action):
            return None
        first_part = action.removeprefix("add_barcode+")

        if "->" not in first_part:
            return None

        parts = first_part.split("->")

        return parts[0], parts[1]


class Barcode(Base):
    __tablename__ = "barcode"
    barcode: str = Column(String(128), primary_key=True)
    # Key into the Product table
    product_id: int = Column(Integer, nullable=False)

    def as_json_dict(self) -> dict[str, Any]:
        return {
            "barcode": self.barcode,
            "product_id": self.product_id,
        }

    def __repr__(self):
        return f"{self.barcode=}, {self.product_id=}"


class Store(Base):
    __tablename__ = "store"
    id: int = Column(Integer, primary_key=True)
    name: str = Column(String(128), nullable=False)


class Product(Base):
    __tablename__ = "product"
    id: int = Column(Integer, primary_key=True)
    # Key into the Store table
    store_id: int = Column(Integer, nullable=False)

    name: str = Column(String(128), nullable=False)
    amount_in_storage: int = Column(Integer, nullable=False)
    amount_wanted: int = Column(Integer, nullable=False)
    sort_order: int = Column(Integer, nullable=False)

    def __repr__(self) -> str:
        return f"Product{{{self.id=}, {self.store_id=}, {self.name=}, {self.amount_in_storage=}, {self.amount_wanted=}, {self.sort_order=}}}"

    def as_json_dict(self, session: Session) -> dict[str, Any]:
        barcodes: list[Barcode] = session.query(Barcode).filter(Barcode.product_id == self.id).all()
        first_barcode = None if len(barcodes) == 0 else barcodes[0]
        additional_barcodes = [] if len(barcodes) <= 1 else barcodes[1:]

        store: Optional[Store] = session.query(Store).filter(Store.id == self.store_id).one_or_none()
        if store is None:
            store = session.query(Store).filter(Store.id == UNKNOWN_STORE_ID).one()

        return {
            "barcode": None if first_barcode is None else first_barcode.barcode,
            "naam": self.name,
            "winkel": store.name if store is not None else None,
            "count": self.amount_in_storage,
            "gewenst": self.amount_wanted,
            "id": self.id,
            "sort_order": self.sort_order,
            "other_barcodes": [p.as_json_dict() for p in additional_barcodes],
        }


# @app.route("/restart", methods=["GET"])


@newstyle_app.route("/boodschappenlijst")
def boodschappenlijst():
    with session_maker.begin() as session:
        all_stores: list[Store] = session.query(Store).all()
        store_name_to_missing_products = {}
        for store in all_stores:
            products = get_wanted_products_for_store(session, store)
            store_name_to_missing_products[store.name] = products
            for product in products:
                product.barcode = first_barcode_or(session, product, "<ERROR: NO BARCODE!>")

        email_text = ""
        email_addresses = json.loads(get_email_setting(session).value)
        if len(email_addresses) > 0:
            email_text = f"mailto:{urllib.parse.quote(email_addresses[0])}?subject={urllib.parse.quote('Boodschappenlijst van ' + datetime.now().strftime('%Y-%m-%d'))}&body="

            for name, incorrect_products in store_name_to_missing_products.items():
                email_text += urllib.parse.quote(
                    make_shopping_list(
                        incorrect_products,
                        header=f"{name}: \n",
                    )
                )

            if len(email_addresses) > 1:
                email_text += "&cc="
                for email in email_addresses[1:]:
                    email_text += f"{urllib.parse.quote(email)},"
                email_text = email_text[:-1]  # remove last ','

        return render_template(
            "boodschappenlijst.html",
            store_name_to_missing_products=store_name_to_missing_products,
            scanner_function=get_scan_action_setting(session).value,
            email_text=email_text,
        )


def make_shopping_list(products: list[Product], header: str = "") -> str:
    if len(products) < 1:
        return ""
    return header + "".join(
        f" * {prod.amount_wanted - prod.amount_in_storage} stuks {prod.name}.\n"
        for prod in products
        if prod.amount_wanted - prod.amount_in_storage > 0
    )


def first_barcode_or(session: Session, product: Product, default: str) -> Any:
    first: Optional[Barcode] = session.query(Barcode).filter(Barcode.product_id == product.id).first()

    return default if first is None else first.barcode


@newstyle_app.route("/alle_producten")
def alle_producten():
    with session_maker.begin() as session:
        all_stores: list[Store] = session.query(Store).all()
        per_store: list[Tuple[str, list[Product]]] = []
        for store in all_stores:
            per_store.append((store.name, get_all_products_for_store(session, store)))

        for (name, products) in per_store:
            for product in products:
                others: list[Barcode] = session.query(Barcode).filter(Barcode.product_id == product.id).all()
                first, rest = others[0], others[1:]
                product.barcode = first.barcode
                product.andere_barcodes = ", ".join(b.barcode for b in rest)

        return render_template(
            "alle_producten.html",
            name_productlist=per_store,
            store_list=all_stores,
            scanner_function=get_scan_action_setting(session).value,
        )


@newstyle_app.route("/instellingen", methods=["GET"])
def instellingen():
    with session_maker.begin() as session:
        return render_template(
            "instellingen.html",
            emails=json.loads(get_email_setting(session).value),
            scanner_function=get_scan_action_setting(session).value,
            version=subprocess.check_output(["git", "log", "--oneline", "-n1"]).decode("utf-8"),
        )


@newstyle_app.route("/v2/product/new", methods=["post"])
def v2_product_new():
    json_text: dict[str, Any] = request.json

    try:
        barcode = json_text["barcode"]
        name = json_text["name"]
        have = json_text["have"]
        want = json_text["want"]
        store = json_text["store"]

    except KeyError as e:
        return f"Faulty request! {e!r}"

    with session_maker.begin() as session:
        try:
            have = int(have)
            want = int(want)

            store_obj: Optional[Store] = store_for_name(store, session)
            if store_obj is None:
                raise ValueError(f"Store {store} not recognized.")

        except ValueError as e:
            return f"error: {e}"

        prod = create_basic_product(session, barcode)
        prod.name = name
        prod.amount_wanted = want
        prod.amount_in_storage = have
        prod.store_id = store_obj.id
        prod.sort_order = highest_sort_order_for_store(store_obj.id, session)

        return jsonify(prod.as_json_dict(session))


@newstyle_app.route("/v2/product/remove/<barcode>", methods=["get"])
def v2_product_remove(barcode: str):
    remove_product(barcode)

    with session_maker.begin() as session:
        product: Optional[Product] = query_for_barcode(session, barcode)
        if product is None:
            message = "Product with barcode {barcode} was not found after product_remove call!"
            newstyle_app.logger.info(message)
            return message, 404

        return jsonify(product.as_json_dict(session))


@newstyle_app.route("/v2/product/add/<barcode>", methods=["get"])
def v2_product_add(barcode: str):
    add_product(barcode)

    with session_maker.begin() as session:
        product: Optional[Product] = query_for_barcode(session, barcode)
        if product is None:
            message = f"Product with barcode {barcode} was not found after product_add call!"
            newstyle_app.logger.warning(message)
            return message, 404

        return jsonify(product.as_json_dict(session))


def query_for_all_products(session: Session) -> list[dict[str, Any]]:
    products: list[Product] = session.query(Product).all()

    return [p.as_json_dict(session) for p in products]


@newstyle_app.route("/v2/product/list", methods=["get"])
def v2_product_list():
    with session_maker.begin() as session:
        products = query_for_all_products(session)
        return jsonify(products)


def get_wanted_products_for_store(session: Session, store: Store) -> list[Product]:
    return (session.query(Product)
            .filter(Product.store_id == store.id)
            .filter(Product.amount_in_storage < Product.amount_wanted)
            .order_by(Product.sort_order)
            .all())


def get_all_products_for_store(session: Session, store: Store) -> list[Product]:
    return (session.query(Product)
            .filter(Product.store_id == store.id)
            .order_by(Product.sort_order)
            .all())


@newstyle_app.route("/v2/boodschappenlijst", methods=["get"])
def v2_boodschappenlijst():
    with session_maker.begin() as session:
        all_stores: list[Store] = session.query(Store).all()
        store_name_to_missing_products = {}
        for store in all_stores:
            # ensure the current version of the app still has the three stores it expects.
            store_name_key = {"Lidl": "LIDL", "Plus": "PLUS", "Onbekend": "NONE"}.get(store.name, store.name)

            store_name_to_missing_products[store_name_key] = [p.as_json_dict(session) for p in
                                                              get_wanted_products_for_store(session, store)]

        return jsonify(store_name_to_missing_products)


@newstyle_app.route("/v2/product/<barcode>", methods=["GET"])
def v2_product(barcode: str):
    with session_maker.begin() as session:
        product: Optional[Product] = query_for_barcode(session, barcode)
        if product is None:
            return f"Could not find product with barcode {barcode}!", 404

        return jsonify(product.as_json_dict(session))


def store_for_name(name: str, session: Session) -> Optional[Store]:
    return session.query(Store).filter(Store.name == name).one_or_none()


def highest_sort_order_for_store(store_id: int, session: Session) -> int:
    last_prod: Optional[Product] = (
        session.query(Product)
        .filter(Product.store_id == store_id)
        .order_by(Product.sort_order.desc())
        .first()
    )

    return 0 if last_prod is None else last_prod.sort_order


@newstyle_app.route("/update_product", methods=["POST"])
def update_product():
    with session_maker.begin() as session:
        barcode = request.json.get("barcode", None)
        if barcode is None:
            return "No barcode in request object!", 400

        prod: Optional[Product] = query_for_barcode(session, barcode)
        if prod is None:
            return f"No product for barcode {barcode}!", 404

        name = request.json.get("naam", None)
        if name is None:
            return f"No `naam` field in request object!", 400
        prod.name = name

        count = request.json.get("count", None)
        if count is None:
            return f"No `count` field in request object!", 400
        try:
            prod.amount_in_storage = int(count)
        except ValueError:
            return f"Count must be an integer! (was: {count})", 400

        wanted = request.json.get("gewenst", None)
        if wanted is None:
            return f"No `gewenst` field in request object!", 400
        try:
            prod.amount_wanted = int(wanted)
        except ValueError:
            return f"Gewenst must be an integer! (was: {wanted})", 400

        store_text = request.json.get("winkel", None)
        if store_text is None:
            return f"No `winkel` field in request object!", 400

        new_store = store_for_name(store_text, session)
        if new_store is None:
            return f"Store with name `{store_text}` could not be found!", 400

        store_changed = prod.store_id != new_store.id

        prod.store_id = new_store.id
        if prod.sort_order is None or store_changed:
            prod.sort_order = highest_sort_order_for_store(prod.store_id, session) + 1

        return f"Updated product to be: {prod}"


@newstyle_app.route("/verwijder/<barcode>", methods=["GET"])
def delete_product(barcode: str):
    with session_maker.begin() as session:
        product = query_for_barcode(session, barcode)

        if product is None:
            return f"Product with barcode {barcode} did not exist at all."

        additionals = session.query(Barcode).filter(Product.id == product.id).all()
        for additional_barcode in additionals:
            session.delete(additional_barcode)

        if product is not None:
            session.delete(product)

    return f"Product met barcode {barcode} is verwijderd."


def query_for_first_above(product: Product, session: Session) -> Optional[Product]:
    return (
        session.query(Product)
        .filter(Product.store_id == product.store_id)
        .filter(Product.sort_order < product.sort_order)
        .order_by(Product.sort_order.desc())
        .first()
    )


def query_for_first_below(product: Product, session: Session) -> Optional[Product]:
    return (
        session.query(Product)
        .filter(Product.store_id == product.store_id)
        .filter(Product.sort_order > product.sort_order)
        .order_by(Product.sort_order)
        .first()
    )


@newstyle_app.route("/move_up/<barcode>", methods=["POST"])
def move_up(barcode: str):
    with session_maker.begin() as session:
        prod = query_for_barcode(session, barcode)
        if prod is None:
            return "Could not find that product!", 404

        in_new_place = query_for_first_above(prod, session)
        if in_new_place is not None:
            prod.sort_order, in_new_place.sort_order = (
                in_new_place.sort_order,
                prod.sort_order,
            )
    return "ok"


@newstyle_app.route("/move_down/<barcode>", methods=["POST"])
def move_down(barcode: str):
    with session_maker.begin() as session:
        prod = query_for_barcode(session, barcode)
        if prod is None:
            return "Could not find that product!", 404

        in_new_place = query_for_first_below(prod, session)
        if in_new_place is not None:
            prod.sort_order, in_new_place.sort_order = (
                in_new_place.sort_order,
                prod.sort_order,
            )
    return "ok"


def get_scan_action_setting(session: Session) -> Setting:
    saved_action: Optional[Setting] = session.query(Setting).filter(Setting.name == Settings.scan_action).one_or_none()
    if saved_action is not None:
        return saved_action

    default = Setting()
    default.name = Settings.scan_action
    default.value = ScanActions.increase
    session.add(default)
    session.flush()

    return default


@newstyle_app.route("/verwijder/<barcode>", methods=["GET"])
def verwijder(barcode: str):
    if barcode is None:
        return f"Did not send barcode to delete."

    with session_maker.begin() as session:
        product = query_for_barcode(session, barcode)
        if product is None:
            return f"Product {barcode} not found."

        product_id = product.id
        session.delete(product)
        barcodes = session.query(Barcode).filter(Product.id == product_id).all()
        for code in barcodes:
            session.delete(code)

        return f"Product met barcode {barcode} is verwijderd."


@newstyle_app.route("/scan/<barcode>", methods=["GET"])
def scan(barcode: str):
    if barcode is None:
        return f"Did not send scanner barcode."

    with session_maker.begin() as session:
        scan_action = get_scan_action_setting(session)
        if scan_action.value == ScanActions.increase:
            return add_product(barcode)
        elif scan_action.value == ScanActions.decrease:
            return remove_product(barcode)

        split_add_result = ScanActions.split_add_barcode(scan_action.value)
        if split_add_result is None:
            return f"Error: Did not understand scan action!", 500

        (barcode_to_add_to, previous_action) = split_add_result

        # check if barcode already exists and remove
        existing: Optional[Barcode] = session.query(Barcode).filter(Barcode.barcode == barcode).one_or_none()
        if existing is not None:
            session.delete(existing)

        # do this in the same session so that rollback will not bring us to an invalid state.
        actual_product: Optional[Product] = query_for_barcode(session, barcode_to_add_to)
        if actual_product is None:
            return "Error: Could not find underlying product for adding barcode to!", 500

        new_barcode = Barcode()
        new_barcode.barcode = barcode
        new_barcode.product_id = actual_product.id

        session.add(new_barcode)

        # do this in the same session so that rollback will not bring us to an invalid state.
        scan_action.value = previous_action

        return f"Added barcode {barcode} to product with barcode {barcode_to_add_to}."


@newstyle_app.route("/barcode_toevoegen/<barcode>", methods=["GET"])
def add_bardcode(barcode: str):
    with session_maker.begin() as session:
        setting = get_scan_action_setting(session)
        if setting.value not in (ScanActions.increase, ScanActions.decrease):
            # we will not nest add barcode actions.
            setting.value = ScanActions.increase

        setting.value = ScanActions.add_barcode(setting.value, barcode)

    return f"Next scanning action will add a barcode to {barcode}."


@newstyle_app.route("/scanner_function_switch/<function>", methods=["GET"])
def scanner_function_switch(function: str):
    if function is None:
        return f"Did not send scanner function."

    # not-refreshed browsers will send this.
    if function == "toevoegen":
        function = ScanActions.increase
    elif function == "weghalen":
        function = ScanActions.decrease

    if (function not in [ScanActions.increase, ScanActions.decrease]
            and not ScanActions.is_add_barcode(function)):
        return f"Unknown scanner function {function}"

    with session_maker.begin() as session:
        settings = get_scan_action_setting(session)
        settings.value = function

    return f"Updated scanner function to be {function}."


def query_for_barcode(session: Session, barcode: str) -> Optional[Product]:
    found_barcode: Optional[Barcode] = session.query(Barcode).filter(Barcode.barcode == barcode).one_or_none()
    if found_barcode is None:
        return None

    return session.query(Product).filter(Product.id == found_barcode.product_id).one_or_none()


def get_highest_sort_order(session: Session) -> int:
    # we have the collumns typed as their values, but actually the types are `smart` Column objects
    # noinspection PyUnresolvedReferences
    return session.query(Product).order_by(Product.sort_order.desc()).first().sort_order


def create_basic_product(session: Session, barcode_text: str) -> Product:
    product = Product()
    product.name = ""
    product.amount_in_storage = 1
    product.amount_wanted = 1
    product.sort_order = get_highest_sort_order(session) + 1
    product.store_id = UNKNOWN_STORE_ID  # unknown store id
    session.add(product)
    session.flush()  # give the product its ID

    barcode = Barcode()
    barcode.barcode = barcode_text
    barcode.product_id = product.id
    session.add(barcode)
    session.flush()

    return product


@newstyle_app.route("/toevoegen/<barcode>", methods=["GET"])
def add_product(barcode: str):
    with session_maker.begin() as session:
        product = query_for_barcode(session, barcode)
        if product is None:
            create_basic_product(session, barcode)
            return f"Product voor barcode {barcode} toegevoegd."

        product.amount_in_storage += 1
        return f"Product met barcode {barcode} heeft nu {product.amount_in_storage} stuks in de kast."


@newstyle_app.route("/weghalen/<barcode>", methods=["GET"])
def remove_product(barcode: str):
    with session_maker.begin() as session:
        product = query_for_barcode(session, barcode)

        if product is None:
            return (
                f"Product with barcode {barcode} does not exist in the database!",
                404,
            )

        product.amount_in_storage -= 1
        return f"Product met barcode {barcode} heeft nu {product.amount_in_storage} stuks in de kast."


def get_email_setting(session: Session) -> Setting:
    return session.query(Setting).filter(Setting.name == Settings.emails).one()


@newstyle_app.route("/toevoegen_email", methods=["POST"])
def toevoegen_email():
    parsed_json = request.json
    if parsed_json is None:
        return f"Could not read json from request!"

    address = parsed_json.get("email")
    if address is None:
        return f"email address was None!"
    address = address.strip()

    with session_maker.begin() as session:
        email_setting = get_email_setting(session)
        emails_list: list[str] = json.loads(email_setting.value)

        if address in emails_list:
            return f"Error: Email with address {address} already exists!"

        emails_list.append(address)
        email_setting.value = json.dumps(emails_list)

    return f"Added email with address {address}."


@newstyle_app.route("/verwijder_email", methods=["POST"])
def verwijder_email():
    parsed_json = request.json
    if parsed_json is None:
        return f"Could not read json from request!"

    address = parsed_json.get("email")
    if address is None:
        return f"Could not get email address from request!"
    address = address.strip()

    with session_maker.begin() as session:
        email_setting = get_email_setting(session)
        emails_list: list[str] = json.loads(email_setting.value)
        if address not in emails_list:
            return f"Error: No email with address {address} found."
        emails_list.remove(address)
        email_setting.value = json.dumps(emails_list)

    return f"Deleted email with address {address}."


@newstyle_app.route("/restart")
def restart():
    subprocess.run(["systemctl", "restart", "voorraadbeheer.service"])

@newstyle_app.route("/")
def hello_world():
    return redirect("/boodschappenlijst")


@newstyle_app.context_processor
def util_methods_definer():
    util_methods = {}

    def str_of_or(value, alternative: str):
        if value is not None:
            return str(value)
        return alternative

    util_methods["str_of_or"] = str_of_or

    return util_methods


def main(host: str):
    global session_maker
    engine = make_engine()
    session_maker = sessionmaker()
    session_maker.configure(bind=engine)

    newstyle_app.run(host=host)
