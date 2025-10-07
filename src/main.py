from classes.Packer import Packer

if __name__ == "__main__":
    packer = Packer("./example/app.py", "./dist")

    packer.pack()