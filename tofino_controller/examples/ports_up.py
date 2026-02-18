from bfrt.controller import Controller
from bfrt.recipes import ports

c = Controller()
ports.add_many(c.session, [
    (14,0,10,"none","disable"),
    (15,0,100,"none","enable"),
])
print("Active:", ports.list_active(c.session))
c.tear_down()
