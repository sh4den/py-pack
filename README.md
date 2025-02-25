# Python Package Chunker (PyPack)

A webpack-like bundler for Python that splits code into optimized chunks.

![image](https://github.com/user-attachments/assets/0598d4c8-88a6-4c68-8b59-a8e0c4f48454)


## Features

-   📦 Code splitting into multiple chunks
-   🔍 Automatic dependency analysis
-   🗺️ Chunk manifest generation
-   🔄 Dynamic chunk loading
-   📊 Smart module assignment based on dependencies
-   ⚡ Webpack-like configuration
-   📜 Regex-based file matching

## Installation

```bash
git clone https://github.com/Inplex-sys/py-pack.git
cd pypack
pip install -e .
```

## Usage

Create a configuration file (`main.py`):

```python
from src.ChunkConfig import ChunkConfig
from src.PythonPacker import PythonPacker

packer = PythonPacker("./src/main.py", "./dist")

# Configure chunks
chunks = [
    ChunkConfig(
        name="vendor",
        entry_points=[],
        includes=[r".*[/\\]vendor[/\\].*\.py"]
    ),
    ChunkConfig(
        name="features",
        entry_points=["./src/features/feature1.py", "./src/features/feature2.py"],
        includes=[r".*[/\\]features[/\\].*\.py"]
    ),
]

packer.configure_chunks(chunks)
packer.pack()
```

## Configuration

### ChunkConfig Options

-   `name`: Name of the chunk
-   `entry_points`: List of entry point files for the chunk
-   `includes`: List of regex patterns to match files that should be included in the chunk

## Output

The bundler generates:

-   Separate chunk files in the `dist` directory
-   A `manifest.json` file containing chunk mapping information
-   Each chunk includes a `load_chunk()` function for dynamic loading

## Dynamic Loading

You can dynamically load chunks in your code:

```python
def load_feature():
    feature_module = load_chunk("features")
    return feature_module.some_feature()
```

## Development

To contribute to the project:

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Submit a pull request

## Requirements

-   Python 3.7+
-   pathlib
-   typing

## License

MIT License

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.
