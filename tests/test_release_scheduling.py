import unittest
from org.metadatacenter.release_support.scheduling import build_edges

class ReleaseSchedulingTest(unittest.TestCase):
    def test_repository_outputs_and_maven_order_are_preserved(self):
        tasks = [dict(variant=v, repository=r, kind=k) for v,r,k in [
            ('release','a','npm-install'), ('release','a','frontend-build'),
            ('release','b','npm-install'), ('release','parent','maven'),
            ('release','libraries','maven'), ('nextDevelopment','a','npm-install'),
            ('nextDevelopment','parent','maven')]]
        self.assertEqual({0:set(),1:{0},2:set(),3:{0,1,2},4:{0,1,2,3},
                          5:set(),6:{5}}, build_edges(tasks))
