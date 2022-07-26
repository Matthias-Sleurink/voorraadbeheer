import enum
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Optional

from flask import Flask, redirect, render_template, request
from sqlalchemy import Column, Enum, Integer, String, create_engine, ForeignKey
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import declarative_base, sessionmaker


Base = declarative_base()

app = Flask(__name__)

CURRENT_VERSION = 3


def make_engine():
    return create_engine(
        "sqlite:///" + str(Path(".") / "voorraad.db")
    )  # TODO: echo for debug ,echo=True


engine = make_engine()


Session = sessionmaker()
Session.configure(bind=engine)

""" TODO:
        Boodschappenlijst-mailfunctie
        Server send to webpage to reload
        Derde winkel
"""

# used to save the add/remove setting when adding a new barcode to a product
vorige_scanner_function: Optional[str] = None


def highest_sort_order():
    with Session.begin() as session:
        # we type the elements of the Product object as if they are their column types but in reality they are Column objects
        # noinspection PyUnresolvedReferences
        lowest_sorted: Product = (
            session.query(Product).order_by(Product.sort_order.desc()).first()
        )
        if lowest_sorted is not None:
            return lowest_sorted.sort_order or 0
        else:
            return 0


class Stores(enum.Enum):
    LIDL = 1
    PLUS = 2


class Product(Base):
    __tablename__ = "product"

    barcode: str = Column(String(128), unique=True)
    naam: Optional[str] = Column(String(128), nullable=True)
    winkel: Optional[Stores] = Column(Enum(Stores), nullable=True)
    count: int = Column(Integer, nullable=False, default=1)
    gewenst: int = Column(Integer, nullable=False, default=1)
    id: int = Column(Integer, primary_key=True)
    sort_order: int = Column(Integer, nullable=False)

    def __repr__(self):
        return f"<Product: {self.barcode=}, {self.naam=}, {self.winkel=}>"


class AdditionalProductBarcode(Base):
    __tablename__ = "additionalbarcodes"

    barcode: str = Column(String(128), unique=True, nullable=False)
    product_id: int = Column(Integer, ForeignKey("product.id"), nullable=False)
    id: int = Column(Integer, primary_key=True)


class TempProduct(Base):
    __tablename__ = "tempproduct"

    naam: str = Column(String(128), nullable=False)
    winkel: Stores = Column(Enum(Stores), nullable=False)
    count: int = Column(Integer, nullable=False, default=0)
    gewenst: int = Column(Integer, nullable=False)
    id: int = Column(Integer, primary_key=True)


class Settings(Base):
    __tablename__ = "settings"
    id: int = Column(Integer, primary_key=True)  # should only ever be 1 of these
    version: int = Column(Integer, nullable=False)
    scanner_functie: str = Column(String(32), nullable=False)
    # defaults are defined in query_for_settings. As that is where the object is made on demand.


class Email(Base):
    __tablename__ = "email"
    id: int = Column(Integer, primary_key=True)
    address: str = Column(String(128), nullable=False)


def query_for_settings(session: Session) -> Settings:
    settings: Optional[Settings] = session.query(Settings).first()
    if settings is None:
        settings = Settings(version=CURRENT_VERSION, scanner_functie="toevoegen")
        session.add(settings)
    return settings


def query_for_barcode(barcode: str, session: Session) -> Optional[Product]:
    in_normal_table = session.query(Product).filter_by(barcode=barcode).first()
    if in_normal_table is not None:
        return in_normal_table

    in_additional_table:Optional[AdditionalProductBarcode] = session.query(AdditionalProductBarcode)\
                                                                    .filter_by(barcode=barcode)\
                                                                    .first()
    if in_additional_table is None:
        return None

    return session.query(Product).filter_by(id=in_additional_table.product_id).first()


def highest_sort_order_for_store(store: Stores, session: Session) -> int:
    # we type the elements of the Product object as if they are their column types but in reality they are Column objects
    # noinspection PyUnresolvedReferences
    last_prod: Product = (
        session.query(Product)
        .filter_by(winkel=store)
        .order_by(Product.sort_order.desc())
        .first()
    )
    return 0 if last_prod.sort_order is None else last_prod.sort_order


def query_for_first_above(product: Product, session) -> Optional[Product]:
    # SQLAlchemy limitations + typing of element as column type not as Column
    # noinspection PyComparisonWithNone,PyUnresolvedReferences
    return (
        session.query(Product)
        .filter_by(winkel=product.winkel)
        .filter(Product.sort_order < product.sort_order)
        .order_by(Product.sort_order.desc())
        .first()
    )


def query_for_first_below(product: Product, session) -> Optional[Product]:
    # SQLAlchemy limitations
    # noinspection PyComparisonWithNone
    return (
        session.query(Product)
        .filter_by(winkel=product.winkel)
        .filter(Product.sort_order > product.sort_order)
        .order_by(Product.sort_order)
        .first()
    )


def make_shopping_list(products: list[Product], header: str = "") -> str:
    if len(products) < 1:
        return ""
    return header + "".join(
        f" * {prod.gewenst - prod.count} stuks {prod.naam}.\n"
        for prod in products
        if prod.gewenst - prod.count > 0
    )


def create_database():
    global engine
    Base.metadata.create_all(engine)
    engine.dispose()
    engine = make_engine()


def run_update_to_1(session: Session) -> Settings:
    Base.metadata.create_all(engine, tables=[Settings.__table__])
    return query_for_settings(session)  # create a default settings object


def run_update_to_2(session: Session):
    Base.metadata.create_all(engine, tables=[Email.__table__])

def run_update_to_3(session: Session):
    Base.metadata.create_all(engine, tables=[AdditionalProductBarcode.__table__])

@app.context_processor
def util_methods_definer():
    util_methods = {}

    def str_of_or(value, alternative: str):
        if value is not None:
            return str(value)
        return alternative

    util_methods["str_of_or"] = str_of_or

    return util_methods


@app.route("/toevoegen/temp", methods=["POST"])
def add_temp_product():
    prod = TempProduct()
    prod.naam = request.json.get("naam")
    prod.winkel = (
        Stores.LIDL if request.json.get("winkel") == "Stores.LIDL" else Stores.PLUS
    )
    prod.gewenst = int(request.json.get("gewenst"))

    with Session.begin() as session:
        session.add(prod)

    return f"{prod.naam} toegevoegd aan de tijdelijke boodschappenlijst"


@app.route("/toevoegen/<barcode>", methods=["GET"])
def add_product(barcode: str):
    with Session.begin() as session:
        entry = query_for_barcode(barcode, session)

        if entry is None:
            session.add(Product(barcode=barcode, sort_order=highest_sort_order() + 1))
            return f"Product voor barcode {barcode} toegevoegd."

        entry.count += 1
        return f"Product met barcode {barcode} heeft nu {entry.count} stuks in de kast."


@app.route("/weghalen/<barcode>", methods=["GET"])
def remove_product(barcode: str):
    with Session.begin() as session:
        entry = query_for_barcode(barcode, session)

        if entry is None:
            return (
                f"Product with barcode {barcode} does not exist in the database!",
                404,
            )

        entry.count -= 1
        return f"Product met barcode {barcode} heeft nu {entry.count} stuks in de kast."


@app.route("/boodschappenlijst")
def boodschappenlijst():
    with Session.begin() as session:

        incorrect_counts_lidl, incorrect_counts_plus, incorrect_counts_no_store = [
            (
                session.query(Product)
                .filter(Product.count < Product.gewenst)
                .filter_by(winkel=winkel)
                .order_by(Product.sort_order)
                .all()
            )
            for winkel in [Stores.LIDL, Stores.PLUS, None]
        ]
        email_text = ""
        email_addresses: [Email] = session.query(Email).all()

        if len(email_addresses) > 0:
            email_text = f"mailto:{urllib.parse.quote(email_addresses[0].address)}?subject={urllib.parse.quote('Boodschappenlijst van ' + datetime.now().strftime('%Y-%m-%d'))}&body="

            for incorrect_products, name in [
                (incorrect_counts_lidl, "Lidl"),
                (incorrect_counts_plus, "Plus"),
                (incorrect_counts_no_store, "Geen winkel"),
            ]:
                email_text += urllib.parse.quote(
                    make_shopping_list(
                        incorrect_products,
                        header=f"{name}: \n",
                    )
                )

            if len(email_addresses) > 1:
                email_text += "&cc="
                for email in email_addresses[1:]:
                    email_text += f"{urllib.parse.quote(email.address)},"
                email_text = email_text[:-1]  # remove last ','

        return render_template(
            "boodschappenlijst.html",
            incorrect_counts_lidl=incorrect_counts_lidl,
            incorrect_counts_plus=incorrect_counts_plus,
            incorrect_counts_no_store=incorrect_counts_no_store,
            scanner_function=query_for_settings(session).scanner_functie,
            email_text=email_text,
        )


@app.route("/alle_producten")
def alle_producten():
    with Session.begin() as session:
        per_store = [
            (
                session.query(Product)
                .filter_by(winkel=store)
                .order_by(Product.sort_order)
                .all()
            )
            for store in [Stores.LIDL, Stores.PLUS, None]
        ]
        with_names = zip(["Lidl", "Plus", "Onbekende winkel"], per_store)
        return render_template(
            "alle_producten.html",
            name_productlist=with_names,
            Stores=Stores,
            scanner_function=query_for_settings(session).scanner_functie,
        )


@app.route("/instellingen", methods=["GET"])
def instellingen():
    with Session.begin() as session:
        return render_template(
            "instellingen.html",
            emails=[e.address for e in session.query(Email).all()],
            scanner_function=query_for_settings(session).scanner_functie,
        )


@app.route("/update_product", methods=["POST"])
def update_product():
    with Session.begin() as session:
        prod = query_for_barcode(request.json.get("barcode"), session)
        prod.naam = request.json.get("naam")
        prod.count = int(request.json.get("count"))
        prod.gewenst = int(request.json.get("gewenst"))
        new_winkel = (
            Stores.LIDL if request.json.get("winkel") == "Stores.LIDL" else Stores.PLUS
        )
        winkel_changed = prod.winkel != new_winkel
        prod.winkel = new_winkel
        if prod.sort_order is None or winkel_changed:
            prod.sort_order = highest_sort_order_for_store(prod.winkel, session) + 1
        return f"Updated product to be: {prod}"


@app.route("/move_up/<barcode>", methods=["POST"])
def move_up(barcode: str):
    with Session.begin() as session:
        prod = query_for_barcode(barcode, session)
        in_new_place = query_for_first_above(prod, session)
        if in_new_place is not None:
            prod.sort_order, in_new_place.sort_order = (
                in_new_place.sort_order,
                prod.sort_order,
            )
    return "ok"


@app.route("/move_down/<barcode>", methods=["POST"])
def move_down(barcode: str):
    with Session.begin() as session:
        prod = query_for_barcode(barcode, session)
        in_new_place = query_for_first_below(prod, session)
        if in_new_place is not None:
            prod.sort_order, in_new_place.sort_order = (
                in_new_place.sort_order,
                prod.sort_order,
            )
    return "ok"


@app.route("/scanner_function_switch/<function>", methods=["GET"])
def scanner_function_switch(function: str):
    if function is None:
        return f"Unknown scanner function {function}"
    if function not in ["toevoegen", "weghalen"] and not function.startswith("barcode_toevoegen"):
        return f"Unknown scanner function {function}"

    with Session.begin() as session:
        settings = query_for_settings(session)

        global vorige_scanner_function
        vorige_scanner_function = settings.scanner_functie
        settings.scanner_functie = function

    return f"Updated scanner function to be {function}"


@app.route("/scan/<barcode>", methods=["GET"])
def scanner_scanned(barcode: str):
    with Session.begin() as session:
        scanner_function = query_for_settings(session).scanner_functie

    if scanner_function == "toevoegen":
        return add_product(barcode)
    elif scanner_function == "weghalen":
        return remove_product(barcode)

    barcode_to_add_to = scanner_function.removeprefix("barcode_toevoegen+")

    with Session.begin() as session:
        to_add_to = query_for_barcode(barcode_to_add_to, session)
        session.add(AdditionalProductBarcode(barcode=barcode, product_id=to_add_to.id))

    with Session.begin() as session:
        global vorige_scanner_function
        settings = query_for_settings(session)
        settings.scanner_functie = vorige_scanner_function

    return f"Added barcode {barcode} to product with barcode {barcode_to_add_to}."


@app.route("/verwijder/<barcode>", methods=["GET"])
def delete_product(barcode: str):
    with Session.begin() as session:
        product = query_for_barcode(barcode, session)
        if product is not None:
            session.delete(product)
    return f"Product met barcode {barcode} is verwijderd."


@app.route("/toevoegen_email", methods=["POST"])
def add_email():
    address = request.json.get("email")
    if address is None:
        return f"email address was None!"

    with Session.begin() as session:
        optEmail = session.query(Email).filter_by(address=address).first()
        if optEmail is not None:
            return f"Error: Email with address {address} already exists!"

        session.add(Email(address=address))
        return f"Added email with address {address}."


@app.route("/verwijder_email", methods=["POST"])
def remove_email():
    address = request.json.get("email")
    if address is None:
        return f"email address was None!"

    with Session.begin() as session:
        optEmail = session.query(Email).filter_by(address=address).first()
        if optEmail is None:
            return f"Error: No email with address {address} found."

        session.delete(optEmail)
        return f"Deleted email with address {address}."

@app.route("/barcode_toevoegen/<barcode>", methods=["GET"])
def add_barcode(barcode: str):
    global vorige_scanner_function

    with Session.begin() as session:
        settings: Settings = query_for_settings(session)
        vorige_scanner_function = settings.scanner_functie
        settings.scanner_functie = "barcode_toevoegen+" + barcode

    return f"Next scanning action will add a barcode for this product."


@app.route("/")
def hello_world():
    return redirect("/boodschappenlijst")


with Session.begin() as session:

    if engine == ":memory:":
        create_database()
    else:
        # New Dev machine does not trigger ":memory:" check, even though the database does not exist.
        try:
            session.query(Product).first()
        except OperationalError as o:
            create_database()
    try:
        settings = query_for_settings(session)
    except OperationalError:
        # the first version did not have a settings table so we need this to check if that is the most recent version.
        settings = run_update_to_1(session)

    if settings.version < 2:
        run_update_to_2(session)
        settings.version = 2

    if settings.version < 3:
        run_update_to_3(session)
        settings.version=3


if __name__ == "__main__":
    app.run()
