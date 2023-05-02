from io import StringIO

from lxml import etree

f = StringIO("""<?xml version="1.0"?>
<project xmlns="http://maven.apache.org/POM/4.0.0" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 http://maven.apache.org/maven-v4_0_0.xsd">
  <modelVersion>4.0.0</modelVersion>')

  <groupId>org.metadatacenter</groupId>
  <artifactId>cedar-parent</artifactId>
  <version>2.6.25-SNAPSHOT</version>
  <packaging>pom</packaging>
  
</project>
""")

tree = etree.parse(f)

r = tree.xpath('/x:project/x:version', namespaces={'x':'http://maven.apache.org/POM/4.0.0'})
print(len(r))

print(r[0].tag)
