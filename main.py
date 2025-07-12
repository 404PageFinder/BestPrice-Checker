from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.button import Button
from kivy.uix.textinput import TextInput
from kivy.uix.scrollview import ScrollView
from kivy.uix.popup import Popup
from kivy.uix.progressbar import ProgressBar
from kivy.clock import Clock
from kivy.uix.screenmanager import ScreenManager, Screen
from kivy.uix.anchorlayout import AnchorLayout
from kivy.uix.widget import Widget
from kivy.graphics import Color, Rectangle
from kivy.uix.image import AsyncImage
from kivy.metrics import dp

import sqlite3
import logging
import os
import threading
import re
import requests
from urllib.parse import quote, urljoin
from bs4 import BeautifulSoup
from dataclasses import dataclass
from typing import List, Optional

logging.basicConfig(level=logging.INFO)

@dataclass
class Product:
    name: str
    price: float
    url: str
    store: str
    availability: str
    image_url: Optional[str] = None
    rating: Optional[float] = None

class DatabaseManager:
    def __init__(self, db_path="price_tracker.db"):
        self.db_path = db_path

    def setup(self, user_data_dir):
        self.db_path = os.path.join(user_data_dir, "price_tracker.db")
        self.init_database()

    def init_database(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            search_query TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS price_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER,
            store TEXT NOT NULL,
            price REAL NOT NULL,
            url TEXT NOT NULL,
            availability TEXT,
            rating REAL,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (product_id) REFERENCES products (id)
        )''')
        conn.commit()
        conn.close()

class ProductCard(BoxLayout):
    def __init__(self, product: Product, **kwargs):
        super().__init__(**kwargs)
        self.orientation = 'horizontal'
        self.size_hint_y = None
        self.height = dp(120)
        self.spacing = dp(10)
        self.padding = dp(10)

        with self.canvas.before:
            Color(0.95, 0.95, 0.95, 1)
            self.rect = Rectangle(size=self.size, pos=self.pos)
        self.bind(size=self._update_rect, pos=self._update_rect)

        if product.image_url:
            img = AsyncImage(source=product.image_url, size_hint_x=0.2)
            self.add_widget(img)

        info_layout = BoxLayout(orientation='vertical', size_hint_x=0.8)
        name_label = Label(text=product.name[:50] + "..." if len(product.name) > 50 else product.name, halign='left', valign='top', size_hint_y=0.4)
        price_label = Label(text=f"${product.price:.2f}", size_hint_y=0.3)
        store_label = Label(text=product.store, size_hint_y=0.3)
        rating_label = Label(text=f"★ {product.rating:.1f}" if product.rating else "No rating", size_hint_y=0.3)
        availability_label = Label(text=product.availability, size_hint_y=0.3)

        info_layout.add_widget(name_label)
        info_layout.add_widget(price_label)
        info_layout.add_widget(store_label)
        info_layout.add_widget(rating_label)
        info_layout.add_widget(availability_label)
        self.add_widget(info_layout)

    def _update_rect(self, instance, value):
        self.rect.pos = instance.pos
        self.rect.size = instance.size

class SearchScreen(Screen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.name = 'search'

        main_layout = BoxLayout(orientation='vertical', padding=dp(20), spacing=dp(15))
        self.search_input = TextInput(hint_text='Enter product name...', multiline=False, size_hint_y=None, height=dp(40))
        search_btn = Button(text='Search', size_hint_y=None, height=dp(40))
        search_btn.bind(on_press=self.search_products)
        self.progress_bar = ProgressBar(size_hint_y=None, height=dp(10), opacity=0)
        self.results_layout = BoxLayout(orientation='vertical', size_hint_y=None, spacing=dp(10))
        self.results_layout.bind(minimum_height=self.results_layout.setter('height'))
        scroll = ScrollView()
        scroll.add_widget(self.results_layout)

        main_layout.add_widget(self.search_input)
        main_layout.add_widget(search_btn)
        main_layout.add_widget(self.progress_bar)
        main_layout.add_widget(scroll)
        self.add_widget(main_layout)

    def search_products(self, instance):
        product_name = self.search_input.text.strip()
        if not product_name:
            self.show_popup("Error", "Please enter a product name")
            return

        self.progress_bar.opacity = 1
        self.results_layout.clear_widgets()
        threading.Thread(target=self._search_thread, args=(product_name,), daemon=True).start()

    def _search_thread(self, product_name):
        try:
            scraper = EcommerceScraper()
            products = scraper.scrape_amazon(product_name) + scraper.scrape_ebay(product_name)
            Clock.schedule_once(lambda dt: self._update_results(products))
        except Exception as e:
            logging.error(f"Search error: {e}")
            Clock.schedule_once(lambda dt: self.show_popup("Error", "Search failed."))

    def _update_results(self, products):
        self.progress_bar.opacity = 0
        self.results_layout.clear_widgets()
        if not products:
            self.results_layout.add_widget(Label(text="No products found", size_hint_y=None, height=dp(40)))
            return
        for product in products:
            self.results_layout.add_widget(ProductCard(product))

    def show_popup(self, title, message):
        popup = Popup(title=title, content=Label(text=message), size_hint=(0.8, 0.4))
        popup.open()

class EcommerceScraper:
    def __init__(self):
        self.headers = {'User-Agent': 'Mozilla/5.0'}
        self.session = requests.Session()
        self.session.headers.update(self.headers)

    def extract_price(self, price_text: str) -> float:
        price_match = re.search(r'[\d,.]+', price_text.replace(',', ''))
        return float(price_match.group()) if price_match else 0.0

    def scrape_amazon(self, product_name: str) -> List[Product]:
        products = []
        try:
            url = f"https://www.amazon.com/s?k={quote(product_name)}"
            resp = self.session.get(url, timeout=10)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.content, 'html.parser')
                for item in soup.select('[data-component-type="s-search-result"]')[:3]:
                    name_elem = item.find('h2')
                    price_elem = item.select_one('.a-price-whole')
                    if not name_elem or not price_elem:
                        continue
                    name = name_elem.text.strip()
                    price = self.extract_price(price_elem.text.strip())
                    link = urljoin("https://www.amazon.com", name_elem.find('a')['href'])
                    products.append(Product(name, price, link, "Amazon", "In Stock"))
        except Exception as e:
            logging.warning(f"Amazon scrape failed: {e}")
        return products

    def scrape_ebay(self, product_name: str) -> List[Product]:
        products = []
        try:
            url = f"https://www.ebay.com/sch/i.html?_nkw={quote(product_name)}"
            resp = self.session.get(url, timeout=10)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.content, 'html.parser')
                for item in soup.select('.s-item__info')[:3]:
                    name_elem = item.select_one('.s-item__title')
                    price_elem = item.select_one('.s-item__price')
                    link_elem = item.find('a')
                    if not name_elem or not price_elem:
                        continue
                    name = name_elem.text.strip()
                    price = self.extract_price(price_elem.text.strip())
                    link = link_elem['href'] if link_elem else ''
                    products.append(Product(name, price, link, "eBay", "Available"))
        except Exception as e:
            logging.warning(f"eBay scrape failed: {e}")
        return products

class PriceComparisonApp(App):
    def build(self):
        self.db = DatabaseManager()
        self.db.setup(self.user_data_dir)
        sm = ScreenManager()
        sm.add_widget(SearchScreen())
        return sm

if __name__ == '__main__':
    PriceComparisonApp().run()
