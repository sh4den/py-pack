from classes.Packer import Packer

if __name__ == "__main__":
    packer = Packer("./alpha/src/main.py", "./dist")

    packer.pack()