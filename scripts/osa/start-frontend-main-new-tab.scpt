tell application "iTerm2"
  tell current window
    set newTab to (create tab with default profile)
    tell current session of newTab
      write text "cd $CEDAR_HOME/cedar-template-editor"
      write text "gulp & echo $! > ~/.cedar/pid-frontend-main.txt"
    end tell
  end tell
end tell