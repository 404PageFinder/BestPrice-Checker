import requests
from bs4 import BeautifulSoup
import json
import time
from datetime import datetime, timedelta
import sqlite3
import logging
from dataclasses import dataclass
from typing import List, Dict, Optional
import re
from urllib.parse import urljoin, quote
import threading
from concurrent.futures import ThreadPoolExecutor
import os

# Kivy imports
from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.button import Button
from kivy.uix.textinput import TextInput
from kivy.uix.scrollview import ScrollView
from kivy.uix.popup import Popup
from kivy.uix.spinner import Spinner
from kivy.uix.switch import Switch
from kivy.uix.progressbar import ProgressBar
from kivy.clock import Clock
from kivy.uix.screenmanager import ScreenManager, Screen
from kivy.uix.card import Card
from kivy.metrics import dp
from kivy.uix.anchorlayout import AnchorLayout
from kivy.uix.widget import Widget
from kivy.graphics import Color, Rectangle
from kivy.uix.image import AsyncImage

# Configure logging for mobile
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)

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
        # Use app directory for mobile
        from kivy.app import App
        if App.get_running_app():
            app_dir = App.get_running_app().user_data_dir
            self.db_path = os.path.join(app_dir, "price_tracker.db")
        else:
            self.db_path = db_path
        self.init_database()
    
    def init_database(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                search_query TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS price_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id INTEGER,
                store TEXT NOT NULL,
                price REAL NOT NULL,
                url TEXT NOT NULL,
                availability TEXT,
                rating REAL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (product_id) REFERENCES products (id)
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS scheduled_searches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_name TEXT NOT NULL,
                schedule_type TEXT NOT NULL,
                schedule_time TEXT NOT NULL,
                email_notifications BOOLEAN DEFAULT 0,
                active BOOLEAN DEFAULT 1
            )
        ''')
        
        conn.commit()
        conn.close()
    
    def save_product(self, product_name: str, search_query: str) -> int:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO products (name, search_query) VALUES (?, ?)",
            (product_name, search_query)
        )
        product_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return product_id
    
    def save_price_data(self, product_id: int, products: List[Product]):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        for product in products:
            cursor.execute('''
                INSERT INTO price_history 
                (product_id, store, price, url, availability, rating)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (product_id, product.store, product.price, product.url, 
                  product.availability, product.rating))
        
        conn.commit()
        conn.close()

class EcommerceScraper:
    def __init__(self):
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Linux; Android 10; SM-G973F) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Mobile Safari/537.36'
        }
        self.session = requests.Session()
        self.session.headers.update(self.headers)
    
    def extract_price(self, price_text: str) -> float:
        if not price_text:
            return 0.0
        
        price_match = re.search(r'[\d,]+\.?\d*', price_text.replace(',', ''))
        if price_match:
            return float(price_match.group())
        return 0.0
    
    def scrape_amazon(self, product_name: str) -> List[Product]:
        products = []
        try:
            search_url = f"https://www.amazon.com/s?k={quote(product_name)}"
            response = self.session.get(search_url, timeout=15)
            
            if response.status_code == 200:
                soup = BeautifulSoup(response.content, 'html.parser')
                product_containers = soup.find_all('div', {'data-component-type': 's-search-result'})
                
                for container in product_containers[:3]:  # Limit for mobile
                    try:
                        name_elem = container.find('h2', class_='s-size-mini')
                        if not name_elem:
                            continue
                        name = name_elem.get_text(strip=True)
                        
                        price_elem = container.find('span', class_='a-price-whole')
                        if not price_elem:
                            continue
                        price = self.extract_price(price_elem.get_text(strip=True))
                        
                        link_elem = container.find('h2').find('a')
                        url = urljoin('https://www.amazon.com', link_elem.get('href', ''))
                        
                        # Get image
                        img_elem = container.find('img')
                        image_url = img_elem.get('src') if img_elem else None
                        
                        rating_elem = container.find('span', class_='a-icon-alt')
                        rating = None
                        if rating_elem:
                            rating_text = rating_elem.get_text()
                            rating_match = re.search(r'(\d+\.?\d*)', rating_text)
                            if rating_match:
                                rating = float(rating_match.group(1))
                        
                        products.append(Product(
                            name=name,
                            price=price,
                            url=url,
                            store="Amazon",
                            availability="In Stock",
                            image_url=image_url,
                            rating=rating
                        ))
                        
                    except Exception as e:
                        logging.warning(f"Error parsing Amazon product: {e}")
                        continue
        
        except Exception as e:
            logging.error(f"Error scraping Amazon: {e}")
        
        return products
    
    def scrape_ebay(self, product_name: str) -> List[Product]:
        products = []
        try:
            search_url = f"https://www.ebay.com/sch/i.html?_nkw={quote(product_name)}"
            response = self.session.get(search_url, timeout=15)
            
            if response.status_code == 200:
                soup = BeautifulSoup(response.content, 'html.parser')
                product_containers = soup.find_all('div', class_='s-item__wrapper')
                
                for container in product_containers[:3]:
                    try:
                        name_elem = container.find('h3', class_='s-item__title')
                        if not name_elem:
                            continue
                        name = name_elem.get_text(strip=True)
                        
                        price_elem = container.find('span', class_='s-item__price')
                        if not price_elem:
                            continue
                        price = self.extract_price(price_elem.get_text(strip=True))
                        
                        link_elem = container.find('a', class_='s-item__link')
                        url = link_elem.get('href', '') if link_elem else ''
                        
                        # Get image
                        img_elem = container.find('img')
                        image_url = img_elem.get('src') if img_elem else None
                        
                        products.append(Product(
                            name=name,
                            price=price,
                            url=url,
                            store="eBay",
                            availability="Available",
                            image_url=image_url
                        ))
                        
                    except Exception as e:
                        logging.warning(f"Error parsing eBay product: {e}")
                        continue
        
        except Exception as e:
            logging.error(f"Error scraping eBay: {e}")
        
        return products

class ProductCard(BoxLayout):
    def __init__(self, product: Product, **kwargs):
        super().__init__(**kwargs)
        self.orientation = 'horizontal'
        self.size_hint_y = None
        self.height = dp(120)
        self.spacing = dp(10)
        self.padding = dp(10)
        
        # Add colored background
        with self.canvas.before:
            Color(0.95, 0.95, 0.95, 1)
            self.rect = Rectangle(size=self.size, pos=self.pos)
        
        self.bind(size=self._update_rect, pos=self._update_rect)
        
        # Product image
        if product.image_url:
            img = AsyncImage(source=product.image_url, size_hint_x=0.2)
            self.add_widget(img)
        
        # Product info
        info_layout = BoxLayout(orientation='vertical', size_hint_x=0.8)
        
        # Product name
        name_label = Label(
            text=product.name[:50] + "..." if len(product.name) > 50 else product.name,
            text_size=(None, None),
            halign='left',
            valign='top',
            size_hint_y=0.4
        )
        info_layout.add_widget(name_label)
        
        # Price and store
        price_layout = BoxLayout(orientation='horizontal', size_hint_y=0.3)
        price_label = Label(
            text=f"${product.price:.2f}",
            bold=True,
            size_hint_x=0.5
        )
        store_label = Label(
            text=product.store,
            color=(0.2, 0.6, 0.8, 1),
            size_hint_x=0.5
        )
        price_layout.add_widget(price_label)
        price_layout.add_widget(store_label)
        info_layout.add_widget(price_layout)
        
        # Rating and availability
        bottom_layout = BoxLayout(orientation='horizontal', size_hint_y=0.3)
        rating_text = f"★ {product.rating:.1f}" if product.rating else "No rating"
        rating_label = Label(
            text=rating_text,
            size_hint_x=0.5
        )
        availability_label = Label(
            text=product.availability,
            color=(0.2, 0.8, 0.2, 1),
            size_hint_x=0.5
        )
        bottom_layout.add_widget(rating_label)
        bottom_layout.add_widget(availability_label)
        info_layout.add_widget(bottom_layout)
        
        self.add_widget(info_layout)
    
    def _update_rect(self, instance, value):
        self.rect.pos = instance.pos
        self.rect.size = instance.size

class SearchScreen(Screen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.name = 'search'
        
        main_layout = BoxLayout(orientation='vertical', padding=dp(20), spacing=dp(15))
        
        # Title
        title = Label(
            text='🛒 Price Comparison',
            font_size=dp(24),
            bold=True,
            size_hint_y=None,
            height=dp(50)
        )
        main_layout.add_widget(title)
        
        # Search input
        search_layout = BoxLayout(orientation='horizontal', size_hint_y=None, height=dp(50))
        self.search_input = TextInput(
            hint_text='Enter product name...',
            multiline=False,
            size_hint_x=0.7
        )
        search_btn = Button(
            text='Search',
            size_hint_x=0.3,
            background_color=(0.2, 0.6, 0.8, 1)
        )
        search_btn.bind(on_press=self.search_products)
        search_layout.add_widget(self.search_input)
        search_layout.add_widget(search_btn)
        main_layout.add_widget(search_layout)
        
        # Progress bar
        self.progress_bar = ProgressBar(
            size_hint_y=None,
            height=dp(10),
            opacity=0
        )
        main_layout.add_widget(self.progress_bar)
        
        # Results scroll view
        self.results_scroll = ScrollView()
        self.results_layout = BoxLayout(orientation='vertical', size_hint_y=None, spacing=dp(10))
        self.results_layout.bind(minimum_height=self.results_layout.setter('height'))
        self.results_scroll.add_widget(self.results_layout)
        main_layout.add_widget(self.results_scroll)
        
        self.add_widget(main_layout)
    
    def search_products(self, instance):
        product_name = self.search_input.text.strip()
        if not product_name:
            self.show_popup("Error", "Please enter a product name")
            return
        
        # Show progress
        self.progress_bar.opacity = 1
        self.results_layout.clear_widgets()
        
        # Add loading message
        loading_label = Label(
            text="Searching products...",
            size_hint_y=None,
            height=dp(50)
        )
        self.results_layout.add_widget(loading_label)
        
        # Start search in background
        thread = threading.Thread(target=self._search_thread, args=(product_name,))
        thread.daemon = True
        thread.start()
    
    def _search_thread(self, product_name):
        try:
            app = App.get_running_app()
            scraper = EcommerceScraper()
            
            all_products = []
            
            # Search Amazon
            amazon_products = scraper.scrape_amazon(product_name)
            all_products.extend(amazon_products)
            
            # Search eBay
            ebay_products = scraper.scrape_ebay(product_name)
            all_products.extend(ebay_products)
            
            # Schedule UI update
            Clock.schedule_once(lambda dt: self._update_results(all_products, product_name))
            
        except Exception as e:
            logging.error(f"Search error: {e}")
            Clock.schedule_once(lambda dt: self._show_error("Search failed. Please try again."))
    
    def _update_results(self, products, product_name):
        self.progress_bar.opacity = 0
        self.results_layout.clear_widgets()
        
        if not products:
            no_results = Label(
                text="No products found",
                size_hint_y=None,
                height=dp(50)
            )
            self.results_layout.add_widget(no_results)
            return
        
        # Sort by price
        valid_products = [p for p in products if p.price > 0]
        sorted_products = sorted(valid_products, key=lambda p: p.price)
        
        # Add best deal header
        if sorted_products:
            best_deal = sorted_products[0]
            header = Label(
                text=f"🏆 Best Deal: {best_deal.store} - ${best_deal.price:.2f}",
                bold=True,
                size_hint_y=None,
                height=dp(40),
                color=(0.2, 0.8, 0.2, 1)
            )
            self.results_layout.add_widget(header)
        
        # Add product cards
        for product in sorted_products:
            card = ProductCard(product)
            self.results_layout.add_widget(card)
        
        # Save to database
        try:
            app = App.get_running_app()
            product_id = app.db.save_product(product_name, product_name)
            app.db.save_price_data(product_id, products)
        except Exception as e:
            logging.error(f"Database error: {e}")
    
    def _show_error(self, message):
        self.progress_bar.opacity = 0
        self.results_layout.clear_widgets()
        error_label = Label(
            text=message,
            size_hint_y=None,
            height=dp(50),
            color=(1, 0.2, 0.2, 1)
        )
        self.results_layout.add_widget(error_label)
    
    def show_popup(self, title, message):
        popup = Popup(
            title=title,
            content=Label(text=message),
            size_hint=(0.8, 0.4)
        )
        popup.open()

class ScheduleScreen(Screen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.name = 'schedule'
        
        main_layout = BoxLayout(orientation='vertical', padding=dp(20), spacing=dp(15))
        
        # Title
        title = Label(
            text='📅 Schedule Search',
            font_size=dp(24),
            bold=True,
            size_hint_y=None,
            height=dp(50)
        )
        main_layout.add_widget(title)
        
        # Product input
        self.product_input = TextInput(
            hint_text='Product name to schedule...',
            multiline=False,
            size_hint_y=None,
            height=dp(50)
        )
        main_layout.add_widget(self.product_input)
        
        # Schedule type
        schedule_layout = BoxLayout(orientation='horizontal', size_hint_y=None, height=dp(50))
        schedule_label = Label(text='Schedule:', size_hint_x=0.3)
        self.schedule_spinner = Spinner(
            text='Daily',
            values=['Daily', 'Weekly', 'Hourly'],
            size_hint_x=0.7
        )
        schedule_layout.add_widget(schedule_label)
        schedule_layout.add_widget(self.schedule_spinner)
        main_layout.add_widget(schedule_layout)
        
        # Time input
        time_layout = BoxLayout(orientation='horizontal', size_hint_y=None, height=dp(50))
        time_label = Label(text='Time:', size_hint_x=0.3)
        self.time_input = TextInput(
            hint_text='HH:MM (e.g., 09:00)',
            multiline=False,
            size_hint_x=0.7
        )
        time_layout.add_widget(time_label)
        time_layout.add_widget(self.time_input)
        main_layout.add_widget(time_layout)
        
        # Notification switch
        notification_layout = BoxLayout(orientation='horizontal', size_hint_y=None, height=dp(50))
        notification_label = Label(text='Notifications:', size_hint_x=0.7)
        self.notification_switch = Switch(size_hint_x=0.3)
        notification_layout.add_widget(notification_label)
        notification_layout.add_widget(self.notification_switch)
        main_layout.add_widget(notification_layout)
        
        # Schedule button
        schedule_btn = Button(
            text='Schedule Search',
            size_hint_y=None,
            height=dp(50),
            background_color=(0.2, 0.6, 0.8, 1)
        )
        schedule_btn.bind(on_press=self.schedule_search)
        main_layout.add_widget(schedule_btn)
        
        # Spacer
        main_layout.add_widget(Widget())
        
        self.add_widget(main_layout)
    
    def schedule_search(self, instance):
        product_name = self.product_input.text.strip()
        schedule_type = self.schedule_spinner.text.lower()
        schedule_time = self.time_input.text.strip()
        notifications = self.notification_switch.active
        
        if not product_name:
            self.show_popup("Error", "Please enter a product name")
            return
        
        if schedule_type in ['daily', 'weekly'] and not schedule_time:
            self.show_popup("Error", "Please enter a time")
            return
        
        try:
            app = App.get_running_app()
            conn = sqlite3.connect(app.db.db_path)
            cursor = conn.cursor()
            
            cursor.execute('''
                INSERT INTO scheduled_searches 
                (product_name, schedule_type, schedule_time, email_notifications)
                VALUES (?, ?, ?, ?)
            ''', (product_name, schedule_type, schedule_time, notifications))
            
            conn.commit()
            conn.close()
            
            self.show_popup("Success", f"Scheduled search for '{product_name}'")
            
            # Clear inputs
            self.product_input.text = ""
            self.time_input.text = ""
            self.notification_switch.active = False
            
        except Exception as e:
            logging.error(f"Schedule error: {e}")
            self.show_popup("Error", "Failed to schedule search")
    
    def show_popup(self, title, message):
        popup = Popup(
            title=title,
            content=Label(text=message),
            size_hint=(0.8, 0.4)
        )
        popup.open()

class HistoryScreen(Screen):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.name = 'history'
        
        main_layout = BoxLayout(orientation='vertical', padding=dp(20), spacing=dp(15))
        
        # Title
        title = Label(
            text='📊 Search History',
            font_size=dp(24),
            bold=True,
            size_hint_y=None,
            height=dp(50)
        )
        main_layout.add_widget(title)
        
        # Refresh button
        refresh_btn = Button(
            text='Refresh',
            size_hint_y=None,
            height=dp(40),
            background_color=(0.2, 0.6, 0.8, 1)
        )
        refresh_btn.bind(on_press=self.load_history)
        main_layout.add_widget(refresh_btn)
        
        # History scroll view
        self.history_scroll = ScrollView()
        self.history_layout = BoxLayout(orientation='vertical', size_hint_y=None, spacing=dp(10))
        self.history_layout.bind(minimum_height=self.history_layout.setter('height'))
        self.history_scroll.add_widget(self.history_layout)
        main_layout.add_widget(self.history_scroll)
        
        self.add_widget(main_layout)
    
    def on_enter(self):
        self.load_history()
    
    def load_history(self, instance=None):
        self.history_layout.clear_widgets()
        
        try:
            app = App.get_running_app()
            conn = sqlite3.connect(app.db.db_path)
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT p.name, ph.store, ph.price, ph.timestamp
                FROM products p
                JOIN price_history ph ON p.id = ph.product_id
                ORDER BY ph.timestamp DESC
                LIMIT 50
            ''')
            
            results = cursor.fetchall()
            conn.close()
            
            if not results:
                no_history = Label(
                    text="No search history found",
                    size_hint_y=None,
                    height=dp(50)
                )
                self.history_layout.add_widget(no_history)
                return
            
            for product_name, store, price, timestamp in results:
                history_item = BoxLayout(
                    orientation='vertical',
                    size_hint_y=None,
                    height=dp(80),
                    padding=dp(10)
                )
                
                # Add background
                with history_item.canvas.before:
                    Color(0.9, 0.9, 0.9, 1)
                    history_item.rect = Rectangle(size=history_item.size, pos=history_item.pos)
                
                history_item.bind(size=lambda instance, value: setattr(instance.rect, 'size', value))
                history_item.bind(pos=lambda instance, value: setattr(instance.rect, 'pos', value))
                
                name_label = Label(
                    text=product_name[:40] + "..." if len(product_name) > 40 else product_name,
                    bold=True,
                    size_hint_y=0.5
                )
                
                info_label = Label(
                    text=f"{store} - ${price:.2f} - {timestamp[:16]}",
                    size_hint_y=0.5
                )
                
                history_item.add_widget(name_label)
                history_item.add_widget(info_label)
                self.history_layout.add_widget(history_item)
        
        except Exception as e:
            logging.error(f"History error: {e}")
            error_label = Label(
                text="Error loading history",
                size_hint_y=None,
                height=dp(50),
                color=(1, 0.2, 0.2, 1)
            )
            self.history_layout.add_widget(error_label)

class PriceComparisonApp(App):
    def build(self):
        self.title = "Price Comparison App"
        self.db = DatabaseManager()
        
        # Create screen manager
        sm = ScreenManager()
        
        # Add screens
        sm.add_widget(SearchScreen())
        sm.add_widget(ScheduleScreen())
        sm.add_widget(HistoryScreen())
        
        # Main layout with navigation
        main_layout = BoxLayout(orientation='vertical')
        
        # Navigation buttons
        nav_layout = BoxLayout(
            orientation='horizontal',
            size_hint_y=None,
            height=dp(60),
            spacing=dp(5),
            padding=dp(5)
        )
        
        search_btn = Button(
            text='Search',
            background_color=(0.2, 0.6, 0.8, 1)
        )
        search_btn.bind(on_press=lambda x: setattr(sm, 'current', 'search'))
        
        schedule_btn = Button(
            text='Schedule',
            background_color=(0.8, 0.6, 0.2, 1)
        )
        schedule_btn.bind(on_press=lambda x: setattr(sm, 'current', 'schedule'))
        
        history_btn = Button(
            text='History',
            background_color=(0.6, 0.8, 0.2, 1)
        )
        history_btn.bind(on_press=lambda x: setattr(sm, 'current', 'history'))
        
        nav_layout.add_widget(search_btn)
        nav_layout.add_widget(schedule_btn)
        nav_layout.add_widget(history_btn)
        
        # Add navigation background
        with nav_layout.canvas.before:
            Color(0.1, 0.1, 0.1, 1)
            nav_layout.rect = Rectangle(size=nav_layout.size, pos=nav_layout.pos)
        
        nav_layout.bind(size=lambda instance, value: setattr(instance.rect, 'size', value))
        nav_layout.bind(pos=lambda instance, value: setattr(instance.rect, 'pos', value))
        
        main_layout.add_widget(sm)
        main_layout.add_widget(nav_layout)
        
        return main_layout

if __name__ == '__main__':
    PriceComparisonApp().run()
