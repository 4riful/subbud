# SubBud - Bug Bounty Subdomain Management Tool 🐞

**SubBud** is a versatile command-line tool designed to simplify the management of subdomains (collected form various sources) for bug bounty hunters and security researchers. It provides features to add, print, list, delete, and save subdomains for different projects, helping you keep track of your bug bounty targets effectively. 🎯

## Table of Contents 📋

- [Installation](#installation)
- [Usage](#usage)
  - [Adding Subdomains](#adding-subdomains)
  - [Printing Subdomains](#printing-subdomains)
  - [Listing Projects](#listing-projects)
  - [Deleting Projects](#deleting-projects)
  - [Saving Merged Subdomains](#saving-merged-subdomains)
- [Requirements](#requirements)
- [Contributing](#contributing)
- [License](#license)

# Installation 🚀 <a name="installation"></a>

SubBud is available on PyPI, and you can easily install it using pip:

```bash
pip install subbud
```
# Usage 🛠️ <a name="usage"></a>
SubBud offers various operations to manage your subdomains effectively.

## Adding Subdomains ➕<a name="adding-subdomains"></a>

To add subdomains to a project, use the `-o add` operation

```
subbud -p project_name -o add -f subdomainsfrom.txt
```
- `-p project_name`: The name of your project.
- `-o add`: Specifies the operation to add subdomains.
- `-f subdomains.txt`: The file containing subdomains to add.
## Printing Subdomains 🖨️ <a name="printing-subdomains"></a>
To print merged/unique subdomains for a project, use the -o print operation:
```bash
subbud -p project_name -o print
```
- `-p project_name`: The name of your project.
- `-o print`: Specifies the operation to print subdomains.

## Listing Projects 📃<a name="listing-projects"></a>

To list all available projects, use the `-o list` operation:
```
subbud -o list

```
- `-o list`: Lists all available projects

## Deleting Projects ❌<a name="deleting-projects"></a>
To delete a project and its associated subdomains, use the `-o delete `operation:

```
subbud -p project_name -o delete
```
## Saving Merged Subdomains 💾<a name="saving-merged-subdomains"></a>

To save the merged subdomains to a text file on the current directory , use the `-o save` operation: 
it will save it with the current date and project name.


```
subbud -p project_name -o save
```
## Requirements ⚙️<a name="requirements"></a>

- Python 3.x
- Redis Server (Make sure the Redis server is running) 🛠️

## Contributing 🤝<a name="contributing"></a>
Contributions to SubBud are welcome! If you have any suggestions, improvements, or bug fixes, please open an issue or create a pull request !

Inspired by [@jhaddix](https://github.com/jhaddix) ❤️ Sir !

