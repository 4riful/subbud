import argparse
import redis
import os
import concurrent.futures
import time
from datetime import datetime
import socket

def is_redis_server_running(host, port):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(2)
            s.connect((host, port))
        return True
    except Exception:
        return False

class DataStore:
    def __init__(self, host='localhost', port=6379, db=0):
        if not is_redis_server_running(host, port):
            print("❌ Redis server is not running. Please start the Redis server.")
            exit(1)
        self.r = redis.Redis(host=host, port=port, db=db)

    def add_domains(self, project, domains):
        pipeline = self.r.pipeline()
        for domain in domains:
            pipeline.sadd(project, domain)
        pipeline.execute()

    def get_domains(self, project):
        return self.r.smembers(project)

    def get_projects(self):
        # Exclude databases starting with an underscore
        all_keys = self.r.keys('*')
        return [key for key in all_keys if not key.startswith(b'_')]   # Get all keys (project names)

    def delete_project(self, project):
        return self.r.delete(project)

class Project:
    def __init__(self, datastore, name):
        self.datastore = datastore
        self.name = name

    def add_domains_from_file(self, filename):
        if not os.path.exists(filename):
            print("❌ File does not exist.")
            return

        domains_to_add = []
        with open(filename, 'r') as file:
            for line in file:
                domain = line.strip()
                if domain:
                    domains_to_add.append(domain)

        if not domains_to_add:
            print("❌ No domains found in the file.")
            return

        current_domains = self.datastore.get_domains(self.name)
        current_domains = {domain.decode('utf-8') for domain in current_domains}
        new_domains = list(set(domains_to_add) - current_domains)

        if not new_domains:
            print("✅ No new domains found.")
            return

        with concurrent.futures.ThreadPoolExecutor() as executor:
            future_to_domain = {executor.submit(self.datastore.add_domains, self.name, new_domains): domain for domain in new_domains}
            
            added_domains = []
            for future in concurrent.futures.as_completed(future_to_domain):
                domain = future_to_domain[future]
                try:
                    future.result()
                    added_domains.append(domain)
                except Exception as e:
                    print(f"❌ Error adding domain '{domain}': {e}")

        new_domain_count = len(added_domains)
        total_domain_count = len(domains_to_add)
        duplicate_percentage = ((total_domain_count - new_domain_count) / total_domain_count) * 100

        print("✅ Domain Addition Complete")
        print(f"✨ Added {new_domain_count} new domains to '{self.name}' project.")
        print(f"🔍 Duplicates detected: {total_domain_count - new_domain_count}")
        print(f"🔄 Percentage of new domains: {duplicate_percentage:.2f}%")


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

def main():
    parser = argparse.ArgumentParser(description="Manage bug bounty targets")
    parser.add_argument('-p', '--project', help='The project name')
    parser.add_argument('-f', '--file', help='The file containing domains')
    parser.add_argument('-o', '--operation', choices=['add', 'print', 'list', 'delete', 'save'], help='Operation to perform')

    args = parser.parse_args()

    # Check if Redis server is running
    if args.operation is not None and not is_redis_server_running('localhost', 6379):
        print("❌ Redis server is not running. Please start the Redis server.")
        exit(1)

    datastore = DataStore()

    if args.operation == 'list':
        projects = datastore.get_projects()
        if not projects:
            print("❌ No projects found.")
        else:
            print("📋 Available projects:")
            for project in projects:
                print(project.decode('utf-8'))
        return

    if not args.project and args.operation not in ['list', 'delete']:
        print("❌ You must provide a project name.")
        return

    project = Project(datastore, args.project)

    if args.operation == 'add':
        if args.file is None:
            print("❌ You must provide a file with the 'add' operation.")
            return
        project.add_domains_from_file(args.file)
    elif args.operation == 'print':
        project.print_domains()
    elif args.operation == 'delete':
        if datastore.delete_project(args.project):
            print(f"✅ Project '{args.project}' deleted.")
        else:
            print(f"❌ Project '{args.project}' not found.")
    elif args.operation == 'save':
        project.save_printed_domains()

if __name__ == '__main__':
    main()
