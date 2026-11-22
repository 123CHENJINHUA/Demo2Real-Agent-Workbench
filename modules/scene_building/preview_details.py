"""Primitive component layouts for the approximate visual scene."""


def box_components(shape, size, color):
    x,y,z = size
    if shape == "channel":
        bottom = .26*z
        wall = .18*y
        return [
            ("Base",[x,y,bottom],[0.,0.,(bottom-z)/2],color),
            ("LipA",[x,wall,z-bottom],[0.,(y-wall)/2,bottom/2],color),
            ("LipB",[x,wall,z-bottom],[0.,-(y-wall)/2,bottom/2],color),
        ]
    if shape == "framed_panel":
        border = min(x,z)*.055
        edge = [.56,.59,.62]
        return [
            ("Panel",[x-2*border,y*.73,z-2*border],[0.,0.,0.],color),
            ("LeftFrame",[border,y,z-2*border],[-(x-border)/2,0.,0.],edge),
            ("RightFrame",[border,y,z-2*border],[(x-border)/2,0.,0.],edge),
            ("BottomFrame",[x,y,border],[0.,0.,-(z-border)/2],edge),
            ("TopFrame",[x,y,border],[0.,0.,(z-border)/2],edge),
        ]
    if shape == "cable_bundle":
        return [
            ("Connector",[x*.25,y*.45,z],[-x*.35,0.,0.],[.09,.09,.09]),
            ("LeadRed",[x*.72,y*.09,z*.30],[x*.13,y*.15,-z*.35],[.46,.05,.05]),
            ("LeadBlack",[x*.72,y*.09,z*.30],[x*.13,-y*.15,-z*.35],[.06,.06,.06]),
        ]
    return [("Block",size,[0.,0.,0.],color)]
