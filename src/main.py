from classes.Packer import Packer

if __name__ == "__main__":
    packer = Packer("./example/main.py", "./dist")

    packer.pack()
