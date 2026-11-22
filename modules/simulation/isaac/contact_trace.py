"""Record first contact reports involving the task's physical objects."""
import numpy as np


class ContactTrace:
    def __init__(self, prims):
        from omni.physx import get_physx_simulation_interface
        from omni.physx.bindings._physx import ContactEventType
        from pxr import PhysicsSchemaTools, PhysxSchema

        self.time=None
        self.rows=[]
        bodies=set()
        for prim in prims:
            PhysxSchema.PhysxContactReportAPI.Apply(prim).CreateThresholdAttr().Set(0.)
            bodies.add(str(prim.GetPath()))

        def record(headers, data):
            if self.time is None:
                return
            for entry in headers:
                if entry.type != ContactEventType.CONTACT_FOUND or not entry.num_contact_data:
                    continue
                first,second = (str(PhysicsSchemaTools.intToSdfPath(actor)) for actor in (entry.actor0,entry.actor1))
                if first not in bodies and second not in bodies:
                    continue
                start=entry.contact_data_offset
                impulse=max(float(np.linalg.norm(data[i].impulse)) for i in range(start,start+entry.num_contact_data))
                self.rows.append((self.time,first,second,impulse))

        self._subscription=get_physx_simulation_interface().subscribe_contact_report_events(record)

    def arrays(self):
        return {'contact_time_s':np.asarray([row[0] for row in self.rows]),
                'contact_actors':np.asarray([row[1:3] for row in self.rows],dtype=str).reshape(-1,2),
                'contact_impulse_ns':np.asarray([row[3] for row in self.rows])}
