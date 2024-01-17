from flask import Flask, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_caching import Cache


app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///products.db'
app.config['CACHE_TYPE'] = 'simple'  # Use SimpleCache
app.config['CACHE_DEFAULT_TIMEOUT'] = 300  # Set a default cache timeout (seconds)

db = SQLAlchemy(app)
cache = Cache(app)

# Product Model
class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False)
    price = db.Column(db.Float, nullable=False)
    stock_level = db.Column(db.Integer, nullable=False)

# Database Initialization
with app.app_context():
    db.create_all()

#Endpoint for Searching for a particular product
# RESTful API Endpoints
@app.route('/products/<int:product_id>', methods=['GET'])
def get_product(product_id):
    product = Product.query.get(product_id)
    if product:
        return jsonify({
            'id': product.id,
            'name': product.name,
            'price': product.price,
            'stock_level': product.stock_level
        })
    return jsonify({'message': 'Product not found'}), 404

#Endpoint for returning all products
@cache.cached(timeout=600, key_prefix='all_products')
@app.route('/products', methods=['GET'])
def get_products():
    page = request.args.get('page', default=1, type=int)
    limit = request.args.get('limit', default=5, type=int)
    
    products = Product.query.paginate(page=page, per_page=limit, error_out=False)
    if not products.items:
        return jsonify({'message': 'No products available'}), 404
    
    product_list = [{
        'id': product.id,
        'name': product.name,
        'price': product.price,
        'stock_level': product.stock_level
    } for product in products.items]

    # Invalidate cache for the updated product
    cache_key = f'product_{product.id}'
    cache.delete(cache_key)

    return jsonify({'products': product_list})

# Endpoint for updating a particular product data
@app.route('/products/<int:product_id>', methods=['PATCH'])
def update_product(product_id):
    product = Product.query.get(product_id)
    if not product:
        return jsonify({'message': 'Product not found'}), 404

    data = request.json

    # Validate price and stock_level types 
    # Assuming we can't change names
    if 'price' in data and not isinstance(data['price'], (float, int)):
        return jsonify({'message': 'Invalid price type'}), 400

    if 'stock_level' in data and not isinstance(data['stock_level'], int):
        return jsonify({'message': 'Invalid stock_level type'}), 400


    product.price = data.get('price', product.price)
    product.stock_level = data.get('stock_level', product.stock_level)

    db.session.commit()

    return jsonify({'message': 'Product updated successfully'})


# Endpoint for Searching Products
@app.route('/products/search', methods=['GET'])
def search_products():
    name_query = request.args.get('name')
    min_price_query = request.args.get('min_price')
    max_price_query = request.args.get('max_price')

    # Filtering based on name or price range
    products_query = Product.query

    if name_query:
        products_query = products_query.filter(Product.name.ilike(f"%{name_query}%"))

    if min_price_query:
        products_query = products_query.filter(Product.price >= float(min_price_query))

    if max_price_query:
        products_query = products_query.filter(Product.price <= float(max_price_query))

    matched_products = products_query.all()

    if not matched_products:
        return jsonify({'message': 'No matching products found'}), 404

    product_list = [{
        'id': product.id,
        'name': product.name,
        'price': product.price,
        'stock_level': product.stock_level
    } for product in matched_products]

    return jsonify({'products': product_list})

# Global Error Handling
@app.errorhandler(Exception)
def handle_error(error):
    app.logger.error(f"Unexpected error: {error}")

    response = {
        'error': 'Internal Server Error',
        'message': 'An unexpected error occurred on the server.'
    }

    return jsonify(response), 500

# Run the application
if __name__ == '__main__':
    app.run(debug=True)