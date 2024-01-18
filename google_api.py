import os, time
import csv
import multiprocessing
from datetime import datetime, timedelta
from functools import partial

MAX_RETRIES = 3  # Set the maximum number of retries

def fetch_data(date):
    # Assume this function is implemented to fetch data from the Google Ads API for a given date
    # Replace this with the actual implementation of your fetch_data function
    # For demonstration, let's assume it returns some dummy data
    return {'date': date, 'metrics': {'clicks': 100, 'impressions': 1000, 'cost': 50.0}}

def process_date(date, results, retry_count=0):
    start_time = time.time()
    try:
        data = fetch_data(date)
        results.append(data)
    except TimeoutError as e:
        print(f"Timeout error fetching data for date {date}: {str(e)}")
    except Exception as e:
        if "Rate limiting error" in str(e):
            if retry_count < MAX_RETRIES:
                print(f"Rate limiting error for date {date}. Retrying after a delay.")
                time.sleep(5)  # Adjust the delay as needed
                process_date(date, results, retry_count + 1)  # Retry the API call with incremented retry count
            else:
                print(f"Exceeded maximum retry limit for date {date}. Skipping.")
        else:
            print(f"Error fetching data for date {date}: {str(e)}")
    finally:
        end_time = time.time()
        print(f"Time taken for {date}: {end_time - start_time} seconds")

def fetch_and_save_data(start_date, end_date, output_folder):
    manager = multiprocessing.Manager()
    results = manager.list()

    pool = multiprocessing.Pool()

    dates = [start_date + timedelta(days=i) for i in range((end_date - start_date).days + 1)]

    # Use multiprocessing.Pool to parallelize data retrieval
    # Each process will handle a different date
    partial_process_date = partial(process_date, results=results)  # Create a partial function
    results = pool.map(partial_process_date, dates)

    # Save aggregated data to CSV
    csv_file_path = os.path.join(output_folder, 'aggregated_data.csv')
    with open(csv_file_path, 'w', newline='') as csvfile:
        fieldnames = ['date', 'clicks', 'impressions', 'cost']  # Adjust based on actual metrics
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()

        #Tested with the following value for results and it gets saved to csv file
        #results = [{'date': '12-12-2023', 'metrics': {'clicks': 100, 'impressions': 1000, 'cost': 50.0}}]
        for data in results:
            # Access the 'metrics' dictionary within 'data'
            if isinstance(data, dict):
                metrics = data.get('metrics', {})
                flat_data = {'date': data.get('date', ''), **metrics}
                writer.writerow(flat_data)


    print(f"Aggregated data saved to {csv_file_path}")

if __name__ == "__main__":
    # Input: start_date and end_date
    start_date_str = input("Enter start date (YYYY-MM-DD): ")
    end_date_str = input("Enter end date (YYYY-MM-DD): ")

    start_date = datetime.strptime(start_date_str, "%Y-%m-%d")
    end_date = datetime.strptime(end_date_str, "%Y-%m-%d")

    # Specify the output folder
    output_folder = os.path.dirname(os.path.abspath(__file__))

    # Call the fetch_and_save_data function
    fetch_and_save_data(start_date, end_date, output_folder)


    