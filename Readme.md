# Snapshot tool
Snapshot tool is python based tool with graphical user interface which is able to store (and later restore) values of channel access process variables. PVs).

## Dependencies:
 - python 3 (conda python 3.5 was used for development)
 - pyepics module
 - numpy module
 - json module


## Usage
To define a set of PVs which should be saved/restored snapshot tool requires a "request" file. Request files are in the following format (note that macro substitution is possible):

```
examplePv:test-1
examplePv:test-2
$(SYS):test-3
```

After snapshoot is build and deployed as conda package (see section [Instalation](#installation) it can be started with following command:

```
snapshot [-h] [-macros MACROS] [-dir DIR] [--force] [REQUEST_FILE]

positional arguments:
  REQUEST_FILE

optional arguments:
  -h, --help            show this help message and exit
  -macros MACROS, -m MACROS
                        Macros for request file e.g.: "SYS=TEST,DEV=D1"
  -dir DIR, -d DIR      Directory for saved files
  --force, -f           Forces save/restore in case of disconnected PVs

```


## Format of saved files
When PVs values are saved using a GUI, they are stored in file where first line starts with `#` and is followed by meta data (json formating). This is followed by lines with PV names and saved data (one line per PV). Example:

```
#{"keywords": "key1,key2", "comment": "This is comment", "save_time": 1452670573.6637778}
examplePv:test-1,20
examplePv:test-2,30
examplePv:test-3,"string"
examplePv:test-4,[5.0, 6.0, 7.0, 8.0, 9.0, 0.0, 1.0, 2.0, 3.0, 4.0]
```

## Installation
Snapshot tool can be installed as anaconda package. Conda recipe is provided in folder `conda_recipe`.
