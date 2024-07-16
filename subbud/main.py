import argparse
import redis
import os
import concurrent.futures
import socket
import logging
import asyncio
from tqdm import tqdm
from datetime import datetime
from dotenv import load_dotenv
import ssl

# Load environment variables from .env file
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def is_redis_server_running(host, port):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(2)
            s.connect((host, port))
        return True
    except Exception:
        return False

class DataStore:
    def __init__(self, host='localhost', port=6379, db=0, use_ssl=False):
        self.host = host
        self.port = port
        self.db = db
        self.use_ssl = use_ssl
        self._connect()

    def _connect(self):
        if not is_redis_server_running(self.host, self.port):
            raise ConnectionError("❌ Redis server is not running. Please start the Redis server.")
        
        if self.use_ssl:
            self.r = redis.Redis(host=self.host, port=self.port, db=self.db, ssl=True, ssl_cert_reqs=ssl.CERT_NONE)
        else:
            self.r = redis.Redis(host=self.host, port=self.port, db=self.db)

    def add_domains(self, project, domains):
        pipeline = self.r.pipeline()
        for domain in domains:
            pipeline.sadd(project, domain)
        pipeline.execute()

    def get_domains(self, project):
        return self.r.smembers(project)

    def get_projects(self):
        all_keys = self.r.keys('*')
        return [key for key in all_keys if not key.startswith(b'_')]

    def delete_project(self, project):
        return self.r.delete(project)

class Project:
    def __init__(self, datastore, name):
        self.datastore = datastore
        self.name = name

    async def add_domains_from_file(self, filename):
        if not os.path.exists(filename):
            raise FileNotFoundError("❌ File does not exist.")

        domains_to_add = []
        with open(filename, 'r') as file:
            for line in file:
                domain = line.strip()
                if domain:
                    domains_to_add.append(domain)

        if not domains_to_add:
            raise ValueError("❌ No domains found in the file.")

        current_domains = self.datastore.get_domains(self.name)
        current_domains = {domain.decode('utf-8') for domain in current_domains}
        new_domains = list(set(domains_to_add) - current_domains)

        if not new_domains:
            print("✅ No new domains found.")
            return

        await self._add_domains_concurrently(new_domains)

        new_domain_count = len(new_domains)
        total_domain_count = len(domains_to_add)
        duplicate_percentage = ((total_domain_count - new_domain_count) / total_domain_count) * 100

        print("✅ Domain Addition Complete")
        print(f"✨ Added {new_domain_count} new domains to '{self.name}' project.")
        print(f"🔍 Duplicates detected: {total_domain_count - new_domain_count}")
        print(f"🔄 Percentage of new domains: {duplicate_percentage:.2f}%")

    async def _add_domains_concurrently(self, domains):
        with concurrent.futures.ThreadPoolExecutor() as executor:
            loop = asyncio.get_event_loop()
            futures = [
                loop.run_in_executor(executor, self.datastore.add_domains, self.name, [domain])
                for domain in domains
            ]
            for _ in tqdm(asyncio.as_completed(futures), total=len(futures), desc="Adding domains", unit=" domain"):
                await _

    def print_domains(self):
        domains = self.datastore.get_domains(self.name)
        domain_count = len(domains)

        if domain_count > 0:
            print(f"✅ {domain_count} domains found for the '{self.name}' project:")
            for domain in domains:
                print(domain.decode('utf-8'))
        else:
            print("❌ No domains found for this project.")

    def save_printed_domains(self):
        domains = self.datastore.get_domains(self.name)
        domain_count = len(domains)

        if domain_count > 0:
            current_date = datetime.now().strftime('%Y-%m-%d')
            filename = f"{current_date}_{self.name}.txt"
            with open(filename, 'w') as file:
                for domain in domains:
                    file.write(domain.decode('utf-8') + '\n')
            print(f"✅ Printed domains saved to '{filename}'")
        else:
            print("❌ No domains found for this project.")

def list_projects(datastore):
    projects = datastore.get_projects()
    if not projects:
        print("❌ No projects found.")
    else:
        print("📋 Available projects:")
        for project in projects:
            print(project.decode('utf-8'))

def main():
    parser = argparse.ArgumentParser(description="Manage bug bounty targets")
    parser.add_argument('-p', '--project', help='The project name')
    parser.add_argument('-f', '--file', help='The file containing domains')
    parser.add_argument('-o', '--operation', choices=['add', 'print', 'list', 'delete', 'save'], required=True, help='Operation to perform')
    parser.add_argument('--host', default=os.getenv('REDIS_HOST', 'localhost'), help='Redis server host')
    parser.add_argument('--port', type=int, default=os.getenv('REDIS_PORT', 6379), help='Redis server port')
    parser.add_argument('--db', type=int, default=os.getenv('REDIS_DB', 0), help='Redis database number')
    parser.add_argument('--ssl', action='store_true', default=os.getenv('REDIS_SSL', 'false').lower() in ['true', '1'], help='Use SSL for Redis connection')

    args = parser.parse_args()

    if args.operation not in ['list', 'delete'] and not args.project:
        parser.error("❌ You must provide a project name for this operation.")

    try:
        datastore = DataStore(host=args.host, port=args.port, db=args.db, use_ssl=args.ssl)
    except ConnectionError as e:
        print(e)
        return

    if args.operation == 'list':
        list_projects(datastore)
        return

    project = Project(datastore, args.project)

    try:
        if args.operation == 'add':
            if not args.file:
                parser.error("❌ You must provide a file with the 'add' operation.")
            asyncio.run(project.add_domains_from_file(args.file))
        elif args.operation == 'print':
            project.print_domains()
        elif args.operation == 'delete':
            if datastore.delete_project(args.project):
                print(f"✅ Project '{args.project}' deleted.")
            else:
                print(f"❌ Project '{args.project}' not found.")
        elif args.operation == 'save':
            project.save_printed_domains()
    except (FileNotFoundError, ValueError, Exception) as e:
        logging.error(e)

if __name__ == '__main__':
    main()
