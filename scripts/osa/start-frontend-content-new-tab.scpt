tell application "iTerm2"
  tell current window
    set newTab to (create tab with default profile)
    tell current session of newTab
      write text "cd $CEDAR_HOME/cedar-content-distribution"
      write text "ng serve & echo $! > ~/.cedar/pid-frontend-content.txt"
    end tell
  end tell
end tell