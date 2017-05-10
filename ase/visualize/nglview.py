# coding: utf-8

from ase import Atoms
import nglview 

class NGLDisplay:
    '''
    Structure display class providing basic structure/trajectory display 
    in the notebook and optional gui which can be used to enhance its usability.
    It is also possible to extend the functionality of the particular instance
    of the viewer by adding further widgets manipulating the structure.
    '''
    def __init__(self, atoms ):
        from ipywidgets import Dropdown, FloatSlider, IntSlider, HBox, VBox
        self.atoms = atoms
        if isinstance(atoms[0],Atoms):
            # Assume this is a trajectory or struct list
            self.view = nglview.show_asetraj( atoms )
            self.frm=IntSlider(value=0, min=0, max=len(atoms)-1)
            self.frm.observe(self._update_frame)
            self.struct = atoms[0]
        else :
            # Assume this is just a single structure
            self.view = nglview.show_ase( atoms )
            self.struct = atoms
            self.frm = None
        
        self.view._remote_call("setSize", target="Widget", args=["500px", "500px"])
        self.view.add_unitcell()
        self.view.add_spacefill()
        self.view.camera='orthographic'
        self.view.update_spacefill(radiusType='covalent', scale=0.7)
        self.view.center()
        
        self.asel=Dropdown(options=['All']+list(set(self.struct.get_chemical_symbols())),
                           value='All', description='Show')
        
        self.rad=FloatSlider(value=0.8, min=0.0, max=1.5, step=0.01, description='Ball size')

        self.asel.observe(self._select_atom)
        self.rad.observe(self._update_repr)
        
        wdg=[self.asel, self.rad]
        if self.frm :
            wdg.append(self.frm)
        
        self.gui = HBox([self.view, VBox(wdg)])

        
    def _update_repr(self, chg=None):
        self.view.update_spacefill(radiusType='covalent', scale=self.rad.value)        
        

    def _update_frame(self, chg=None):
        self.view.frame=self.frm.value
        return
    
    def _select_atom(self, chg=None):
        sel=self.asel.value
        self.view.remove_spacefill()
        if sel == 'All':
            self.view.add_spacefill(selection='All')
        else :
            self.view.add_spacefill(selection=[n for n,e in enumerate(self.struct.get_chemical_symbols()) if e == sel])
        self._update_repr()
            




def view_ngl( atoms ):
    return NGLDisplay( atoms ).gui
    
