import enum
import json
import sys
import typing
from pathlib import Path
from typing import Optional, List, Dict, Any

from flask import Flask
from sqlalchemy import Column, Enum, Integer, String, create_engine, ForeignKey
from sqlalchemy.orm import declarative_base, sessionmaker, Session

import new_style_db
import new_style_db as newstyle
from new_style_db import Barcode

Base = declarative_base()

app = Flask(__name__)

CURRENT_VERSION = 3


def make_engine():
    return create_engine(
        "sqlite:///" + str(Path(".") / "voorraad.db")
        # ,echo=True
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

    def as_json_str(self) -> str:
        if self == Stores.LIDL:
            return "LIDL"
        elif self == Stores.PLUS:
            return "PLUS"
        else:
            return "NONE"


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

    def as_json_dict(self, session: Session) -> dict[str, typing.Any]:
        other_barcodes: typing.List[AdditionalProductBarcode] =\
            session.query(AdditionalProductBarcode).filter_by(product_id=self.id).all()

        return {
            "barcode": self.barcode,
            "naam": self.naam,
            "winkel": self.winkel if self.winkel is None else self.winkel.as_json_str(),
            "count": self.count,
            "gewenst": self.gewenst,
            "id": self.id,
            "sort_order": self.sort_order,
            "other_barcodes": [p.as_json_dict() for p in other_barcodes],
        }


class AdditionalProductBarcode(Base):
    __tablename__ = "additionalbarcodes"

    barcode: str = Column(String(128), unique=True, nullable=False)
    product_id: int = Column(Integer, ForeignKey("product.id"), nullable=False)
    id: int = Column(Integer, primary_key=True)

    def as_json_dict(self) -> dict[str, typing.Any]:
        return {
            "barcode": self.barcode,
            "product_id": self.product_id,
            "id": self.id,
        }

    def __repr__(self):
        return f"<AdditionalBarcode: {self.barcode=}, {self.product_id=}, {self.id=}>"


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


def query_for_all_products(session: Session) -> List[Dict[str, Any]]:
    products = session.query(Product).all()
    return [p.as_json_dict(session) for p in products]


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

def get_wanted_products_for_store(session: Session, winkel: Optional[Stores]) -> list[Product]:
    return (session.query(Product)
            .filter(Product.count < Product.gewenst)
            .filter_by(winkel=winkel)
            .order_by(Product.sort_order)
            .all())

def oldstyle_to_newstyle():
    newstyle_engine = newstyle.create_database()
    new_sessionmaker = sessionmaker(bind=newstyle_engine)
    old_sessionmaker = Session

    oldstyle_settings_to_newstyle(old_sessionmaker, new_sessionmaker)
    oldstyle_emails_to_newstyle(old_sessionmaker, new_sessionmaker)
    oldstyle_products_to_newstyle(old_sessionmaker, new_sessionmaker)


def oldstyle_products_to_newstyle(old_sessionmaker, new_sessionmaker):
    # There was no stores table, so we insert the basic stores.

    lidl_id, plus_id, unknown_id = 0, 0, 0
    with new_sessionmaker.begin() as session:
        unknown = newstyle.Store(id=newstyle.UNKNOWN_STORE_ID)
        unknown.name = "Onbekend"
        session.add(unknown)

        lidl = newstyle.Store()
        lidl.name = "Lidl"
        session.add(lidl)

        plus = newstyle.Store()
        plus.name = "Plus"
        session.add(plus)

        # flushes the objects, and creates the auto-incremented IDs
        session.flush()
        unknown_id = unknown.id
        plus_id = plus.id
        lidl_id = lidl.id

    with old_sessionmaker.begin() as old_session:
        with new_sessionmaker.begin() as new_session:
            for old_product in old_session.query(Product):
                new_product = newstyle.Product()
                new_product.name = old_product.naam or ""  # name used to be nullable
                new_product.amount_in_storage = old_product.count
                new_product.amount_wanted = old_product.gewenst
                new_product.sort_order = old_product.sort_order

                if old_product.winkel == Stores.LIDL:
                    new_product.store_id = lidl_id
                elif old_product.winkel == Stores.PLUS:
                    new_product.store_id = plus_id
                else:
                    new_product.store_id = unknown_id

                # The add adds the object as a managed object, the flush creates the auto-incremented ID.
                new_session.add(new_product)
                new_session.flush()

                # Issue case 2: Some products have a main barcode that is also an additional barcode for another product.
                #               The previous code first searched primary barcodes, and then only when not found, additional ones.
                #               So, we want this "primary" barcode to replace the previous one that was put in place as
                #               an additional barcode.
                m_duplicate_prim_bc = new_session.query(newstyle.Barcode).filter(
                    Barcode.barcode == old_product.barcode).one_or_none()
                if m_duplicate_prim_bc is not None:
                    app.logger.warning(f"Running fixup: Main barcode for {old_product} equals an additional"
                                       f" barcode {m_duplicate_prim_bc}."
                                       f" The barcode will be adjusted to point to {new_product}.")
                    m_duplicate_prim_bc.product_id = new_product.id
                else:
                    bc = newstyle.Barcode(barcode=old_product.barcode)
                    bc.product_id = new_product.id
                    new_session.add(bc)

                other_barcodes: typing.List[AdditionalProductBarcode] = \
                    old_session.query(AdditionalProductBarcode).filter_by(product_id=old_product.id).all()
                for obc in other_barcodes:
                    # issue case 1: There are some products that have a barcode as primary and as additional barcode.
                    m_duplicate_bc = new_session.query(newstyle.Barcode).filter(
                        Barcode.barcode == obc.barcode).one_or_none()

                    if m_duplicate_bc is not None:
                        if m_duplicate_bc.product_id == new_product.id:
                            # this is already a good barcode, for the product we want, so there is no problem.
                            app.logger.warning(f"Running fixup: Not making Barcode for {old_product},"
                                               f" as the additional barcode {obc} is also the barcode for the product.")
                            continue
                        else:
                            raise ValueError(
                                f"Duplicate barcode found! {old_product=}, {new_product=}, {obc=}, {m_duplicate_bc=}")

                    nbc = newstyle.Barcode(barcode=obc.barcode)
                    nbc.product_id = new_product.id
                    new_session.add(nbc)


def oldstyle_emails_to_newstyle(old_sessionmaker, new_sessionmaker):
    with old_sessionmaker.begin() as old_session:
        emails: List[Email] = old_session.query(Email).all()

        emails_text = json.dumps([email.address for email in emails])

        with new_sessionmaker.begin() as new_session:
            s = new_style_db.Setting()
            s.name = "emails"
            s.value = emails_text
            new_session.add(s)


def oldstyle_settings_to_newstyle(old_sessionmaker, new_sessionmaker):
    with old_sessionmaker.begin() as old_session:
        settings = query_for_settings(old_session)

        kv_pairs = {
            "version": settings.version,
            "scanner_function": settings.scanner_functie,
        }

        with new_sessionmaker.begin() as new_session:
            for k, v in kv_pairs.items():
                s = new_style_db.Setting()
                s.name = k
                s.value = v
                new_session.add(s)


def main():
    if not (Path(".") / "voorraad_newstyle.db").exists():
        oldstyle_to_newstyle()

    params = sys.argv[1:]
    if len(params) == 0 or not params[0].startswith("--host="):
        print("Error: Please provide the host IP with --host=<ip>")
        sys.exit(1)

    host = params[0].removeprefix("--host=")

    newstyle.main(host)


if __name__ == "__main__":
    main()
